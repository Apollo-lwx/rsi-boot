# EngMem-Bench 第二标注者说明

你拿到的是 sheet.csv，每行一道题。请对每一行：

1. 读 question 和 reference_answer。
2. 打开该仓库（克隆地址见 repos.yaml，commit 已钉版）里 gold_anchor
   指向的文件节（格式 `文件::节标题`，同名节看 `@N`），核对答案。
3. verdict 填：
   - correct        参考答案与该节内容一致
   - wrong-answer   该节内容不支持参考答案
   - wrong-anchor   锚点指错了节（答案本身可能在别处是对的）
   - unclear        题目本身有歧义
4. 如果你填了 wrong-* ，请在 annotator_answer 里写你认为的正确答案。
5. 不要和第一标注者讨论，独立判断。

大约 90 题，预计 2-3 小时。
