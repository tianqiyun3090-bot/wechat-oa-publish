# 公众号账号配置

发布脚本只依赖 Python 3.9+ 标准库。图片生成由 Codex 内置 ImageGen 完成，账号文件只用于微信草稿上传。

## 凭据来源

优先级：命令行参数 > 当前进程环境变量 > 仓库外账号文件。默认文件为 `~/.config/wechat-oa-publish/wechat.env`；设置 `XDG_CONFIG_HOME` 时使用它下面的 `wechat-oa-publish/wechat.env`。

通过 `--env-file <path>` 可指定其他文件。为了兼容原始压缩包，未指定文件且新默认文件不存在时，会尝试读取 `~/.workbuddy/wechat.env`。

用户在对话中直接给出的密钥，只传入本次进程；持久保存必须来自用户明确的配置请求。需要长期配置时，让用户在仓库之外填写文件：

```ini
WECHAT_APPID=your_wechat_appid
WECHAT_SECRET=your_wechat_appsecret
```

作者、默认商品和卡片类型保存在可公开的 `config/defaults.json`；也支持 `WECHAT_AUTHOR`、`WECHAT_PRODUCT_ID`、`WECHAT_CARD_TYPE` 覆盖。不在源码仓库保存真实 AppSecret、access_token 或带签名的草稿内容。

## 微信后台条件

核对当前公众号是否有对应的素材、草稿及商品卡能力。上传商品卡使用已有商品 ID；商品本身的发布、价格、库存和上下架仍在微信小店后台管理。

微信返回 IP 白名单错误时，把错误中给出的实际出口 IP 配置到该公众号后台的允许列表，然后重新尝试。凭据无效时核对同一个公众号的 AppID 和 AppSecret。带货能力或商品卡报错时查看公众号带货权限和商品 ID，按微信返回的错误码处理；不要凭错误码猜测已经保存成功。

## 重查已经创建的草稿

```bash
python3 scripts/publish_draft.py --verify-receipt /path/to/publish-receipt.json
```

这只回读微信草稿，不上传新封面或再次创建草稿。回执含有草稿 ID、验证状态和封面素材 ID，属于运行产物，保留在任务输出目录。
