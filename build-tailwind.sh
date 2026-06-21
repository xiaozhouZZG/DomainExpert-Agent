#!/bin/bash
# Tailwind CSS 本地构建脚本
# 使用场景：修改 admin.html 添加新的 Tailwind 类后，运行此脚本重新生成 CSS
# 注意：动态生成的颜色类已在 web/static/src/tailwind.input.css 中声明，会自动保留

cd "$(dirname "$0")"

echo "🔨 构建 Tailwind CSS..."
node_modules/.bin/tailwindcss \
  -i web/static/src/tailwind.input.css \
  -o web/static/tailwind.css \
  --content "./web/**/*.html" \
  --minify

if [ $? -eq 0 ]; then
  echo "✅ 构建完成: web/static/tailwind.css"
  ls -lh web/static/tailwind.css
else
  echo "❌ 构建失败"
  exit 1
fi
