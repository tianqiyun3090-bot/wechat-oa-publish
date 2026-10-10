#!/bin/bash
# macOS：居中裁剪后生成 900×383 PNG 封面，原图保持不变。
set -euo pipefail

INPUT="${1:-}"
OUTPUT="${2:-cover-900x383.png}"
if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
  echo "用法：bash crop_cover.sh <输入图片> [输出PNG]" >&2
  exit 1
fi
if ! command -v sips >/dev/null 2>&1; then
  echo "此脚本需要 macOS 自带 sips；其他平台请提供已裁好的 900×383 封面。" >&2
  exit 1
fi
if [ "$INPUT" -ef "$OUTPUT" ] 2>/dev/null; then
  echo "请为输出封面使用不同于原图的路径。" >&2
  exit 1
fi

WIDTH=$(sips -g pixelWidth "$INPUT" | awk '/pixelWidth/{print $2}')
HEIGHT=$(sips -g pixelHeight "$INPUT" | awk '/pixelHeight/{print $2}')
if ! [[ "$WIDTH" =~ ^[0-9]+$ && "$HEIGHT" =~ ^[0-9]+$ ]] || [ "$WIDTH" -eq 0 ] || [ "$HEIGHT" -eq 0 ]; then
  echo "无法读取图片尺寸。" >&2
  exit 1
fi
TEMP_DIR=$(mktemp -d)
trap 'rm -f "$TEMP_DIR/crop.png"; rmdir "$TEMP_DIR"' EXIT
mkdir -p "$(dirname "$OUTPUT")"
CROP_HEIGHT=$((WIDTH * 383 / 900))
if [ "$CROP_HEIGHT" -le "$HEIGHT" ]; then
  sips --setProperty format png -c "$CROP_HEIGHT" "$WIDTH" "$INPUT" --out "$TEMP_DIR/crop.png" >/dev/null
else
  CROP_WIDTH=$((HEIGHT * 900 / 383))
  sips --setProperty format png -c "$HEIGHT" "$CROP_WIDTH" "$INPUT" --out "$TEMP_DIR/crop.png" >/dev/null
fi
sips -z 383 900 "$TEMP_DIR/crop.png" --out "$OUTPUT" >/dev/null
echo "✅ 封面已生成：$OUTPUT"
sips -g pixelWidth -g pixelHeight "$OUTPUT"
