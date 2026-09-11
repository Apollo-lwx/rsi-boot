"""ISSUE-3：Java 项目画像提取——pom.xml 框架探测 + import vendor 前缀归一化。

实仓验证（aigate-dgp）发现：expertise=com,org,lombok,java,io（包名首段当专长），
Spring 项目 framework 为空（config_scanner 不解析 pom.xml）。
"""

from collections import Counter
from pathlib import Path

from rsi_boot.scanner.code_scanner import aggregate_imports, extract_generic_skeleton, scan_code
from rsi_boot.scanner.config_scanner import scan_configs
from rsi_boot.scanner.profile_generator import build_profile

_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>dgp-gateway</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>com.baomidou</groupId>
      <artifactId>mybatis-plus-boot-starter</artifactId>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""

_JAVA_SOURCE = """package com.example.dgp.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.example.dgp.service.UserService;
import org.springframework.boot.SpringApplication;
import org.springframework.web.bind.annotation.RestController;
import java.util.List;
import java.util.Map;
import lombok.Data;

@RestController
public class UserController {
    public Page list() { return null; }
}
"""


def test_pom_parsed_for_framework(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(_POM, encoding="utf-8")
    insights = scan_configs(tmp_path, [pom], [], [], [])
    assert insights.language == "java"
    assert insights.build_system == "maven"
    assert insights.framework == "spring"          # spring-boot-starter-web → spring
    assert insights.test_framework == "junit"


def test_pom_spring_boot_starter_test_maps_junit(tmp_path):
    """spring-boot-starter-test 聚合 JUnit5 栈（实仓常见，无直接 junit artifact）"""
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><dependencies><dependency>"
        "<groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-test</artifactId>"
        "</dependency></dependencies></project>",
        encoding="utf-8",
    )
    insights = scan_configs(tmp_path, [pom], [], [], [])
    assert insights.test_framework == "junit"


def test_profile_expertise_excludes_full_python_stdlib():
    """stdlib 过滤覆盖完整 Python 标准库（time/copy/urllib/subprocess 等）"""
    counter = Counter({
        "time": 30, "copy": 20, "urllib": 15, "subprocess": 12, "argparse": 11,
        "requests": 25, "pydantic": 18,
    })
    profile = build_profile("u1", "p1", _insights(), "moderate", "weak", import_counter=counter)
    assert "requests" in profile.expertise and "pydantic" in profile.expertise
    for noise in ("time", "copy", "urllib", "subprocess", "argparse"):
        assert noise not in profile.expertise


def test_java_import_vendor_prefix_normalized():
    """com.baomidou → baomidou；org.springframework → springframework；JDK 包不计入"""
    s = extract_generic_skeleton(_JAVA_SOURCE, "UserController.java", ".java")
    assert "baomidou" in s.imports
    assert "springframework" in s.imports
    assert "lombok" in s.imports
    for noise in ("com", "org", "java", "util"):
        assert noise not in s.imports


def test_java_import_static_and_own_package():
    """static import 同样归一化；非 vendor 首段原样保留"""
    source = (
        "import static org.junit.Assert.assertEquals;\n"
        "import lombok.extern.slf4j.Slf4j;\n"
        "public class T {}\n"
    )
    s = extract_generic_skeleton(source, "T.java", ".java")
    assert "junit" in s.imports
    assert "lombok" in s.imports
    assert "org" not in s.imports


def test_profile_expertise_excludes_java_noise():
    """画像 expertise 过滤包名/vendor 噪声，保留真实库标签"""
    counter = Counter({
        "com": 40, "org": 35, "java": 30, "javax": 12, "io": 15, "net": 11,
        "springframework": 25, "baomidou": 18, "lombok": 12,
    })
    profile = build_profile("u1", "p1", _insights(), "moderate", "weak", import_counter=counter)
    assert "springframework" in profile.expertise
    assert "baomidou" in profile.expertise
    for noise in ("com", "org", "java", "javax", "io", "net"):
        assert noise not in profile.expertise


def _insights():
    from rsi_boot.scanner.config_scanner import ConfigInsights

    return ConfigInsights(language="java", framework="spring", build_system="maven")


def test_scan_code_java_end_to_end(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "UserController.java").write_text(_JAVA_SOURCE, encoding="utf-8")
    skeletons = scan_code(tmp_path, [src / "UserController.java"])
    assert len(skeletons) == 1
    counter = aggregate_imports(skeletons)
    assert counter.get("springframework", 0) >= 1
    assert counter.get("com", 0) == 0
