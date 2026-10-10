---
name: wechat-oa-publish
description: 为微信公众号制作文章、使用内置 ImageGen 生成封面、插入指定商品卡，并保存和验证草稿。用户要求公众号图文制作、挂商品或存草稿时使用。
---

# 公众号图文与商品卡

默认作者和商品保存在 [config/defaults.json](config/defaults.json)。用户本次指定的账号、作者、商品、写作风格优先于默认值。这里的“挂商品”是把已有商品插入文章，不负责创建商品或修改小店上架状态。

先判断交付范围：只写稿、制作本地正文与封面，或保存到微信公众号草稿箱。用户已经明确要求保存草稿时，准备好内容后直接执行；只要求预览时停留在本地文件。

## 1. 准备正文

写稿前读取 [references/writing-guide.md](references/writing-guide.md)；以 [assets/article-template.html](assets/article-template.html) 为起点，按用户主题替换正文。商品名从默认配置取得，仅作为文案参考；价格和课程承诺以用户提供的材料或真实商品信息为准。

引用数据或产品能力时核对可靠来源；示例提示词可以自行撰写，明确是示例，不冒充已发生的案例。输出正文 HTML 到当前工作区的交付目录，每次任务使用独立目录。

要挂商品时保留一个 `<!--PRODUCT_CARD-->`，由脚本获取官方商品 DOM。无需商品时移除商品推广段并传 `--no-product-card`。

## 2. 生成封面

新封面使用 Codex **内置 `image_gen` / ImageGen 工具**；可用工具名通常为 `image_gen.imagegen` 或 `image_gen__imagegen`。读取当前环境的 imagegen skill，按它的调用方式生成。默认使用笔记本纸张、手写主文案与荧光笔重点，只放一句清晰文字；用户指定风格时采用用户风格。

生成前告知用户会使用图片生成额度。无需用户提供 `OPENAI_API_KEY`，无需调用外部生图服务或另写生图脚本。内置工具不可用时，报告具体限制，可继续正文或使用用户已有封面；替换生成服务需要用户明确选择。

生成约 1536×1024 的横图，把文字放在中央横带，便于裁剪。工具返回本地路径后，用图像查看工具检查中文和构图。macOS 执行：

```bash
bash <skill-dir>/scripts/crop_cover.sh <generated-image> <output-dir>/cover-900x383.png
```

检查裁剪后的 900×383 图片没有切断标题；将最终图和本次提示词保存到交付目录。裁剪用于排版，不用于移除水印。

## 3. 本地预览或保存草稿

脚本路径相对于本 `SKILL.md` 所在目录解析，不依赖原始开发者机器路径。运行参数可通过 `python3 <skill-dir>/scripts/publish_draft.py --help` 查看。

仅预览时运行 `--dry-run`。该模式只验证和保存本地预览，不取 token、不上传素材、不创建草稿、不要求账号密钥；商品卡保留待插入提示。

```bash
python3 <skill-dir>/scripts/publish_draft.py \
  --content <output-dir>/content.html \
  --title "文章标题" --digest "文章摘要" \
  --cover <output-dir>/cover-900x383.png \
  --output-dir <output-dir>/wechat --dry-run
```

用户授权保存草稿时，凭据来自 `WECHAT_APPID` / `WECHAT_SECRET` 或仓库外的账号配置。用户在对话中给出的密钥仅传入当前进程，不写入技能、工作区、回执或 Git。配置方式见 [references/account-setup.md](references/account-setup.md)。

```bash
python3 <skill-dir>/scripts/publish_draft.py \
  --content <output-dir>/content.html \
  --title "文章标题" --digest "文章摘要" \
  --cover <output-dir>/cover-900x383.png \
  --output-dir <output-dir>/wechat
```

省略 `--product-id` 和 `--author` 时使用默认配置；换商品传 `--product-id`，不挂商品传 `--no-product-card`。脚本取 token、获取官方商品卡、上传封面、创建草稿，立即保存回执，再回读核对标题、作者、摘要、封面和商品卡。不会自动发布或群发文章。

## 4. 失败处理与交付

脚本不自动重试创建草稿。回执已有 `media_id` 时，即使回读失败，也报告草稿已经创建，用下面的命令重查，不重新提交：

```bash
python3 <skill-dir>/scripts/publish_draft.py \
  --verify-receipt <output-dir>/wechat/publish-receipt.json
```

创建请求超时而没有收到 `media_id` 时，先让用户查看公众号后台；回执的 `submitting` 状态会阻止重复提交。只有用户明确要求再新建，才使用 `--force-new-draft` 或新任务目录。

常见微信错误：IP 白名单或凭据问题参考 [references/account-setup.md](references/account-setup.md)；商品卡错误查看商品 ID 和公众号带货能力。展示明确的微信错误码，不输出 token、Secret 或带凭据的 URL。

交付正文 HTML、封面和回执的文件链接，给出草稿 `media_id` 与回读结果。后台路径：[微信公众号后台](https://mp.weixin.qq.com) → 内容管理 → 草稿箱。只有回读通过才称“已验证”；创建成功但回读未完成时分别说明。
