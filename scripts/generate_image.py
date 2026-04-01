"""Генерация изображений через DALL-E 3.

Использование:
    python scripts/generate_image.py "промт для изображения" [--size 1024x1024] [--name filename]

Ключ читается из .env (OPENAI_API_KEY).
Результат сохраняется в assets/images/generated/
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "assets" / "images" / "generated"

SIZES = {
    "square": "1024x1024",
    "landscape": "1792x1024",
    "portrait": "1024x1792",
}


def load_env():
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


def generate(prompt: str, size: str = "1024x1024", name: str | None = None):
    try:
        import openai
    except ImportError:
        print("Установи openai: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key == "ваш-новый-ключ-сюда":
        print("Ошибка: задай OPENAI_API_KEY в .env")
        sys.exit(1)

    client = openai.OpenAI(api_key=api_key)

    print(f"Генерация: {prompt[:80]}...")
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size=size,
        quality="hd",
        n=1,
    )

    image_url = response.data[0].url

    import requests

    img_data = requests.get(image_url, timeout=60).content

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if name:
        filename = name if name.endswith(".png") else f"{name}.png"
    else:
        import hashlib
        h = hashlib.md5(prompt.encode()).hexdigest()[:8]
        filename = f"generated_{h}.png"

    out_path = OUTPUT_DIR / filename
    out_path.write_bytes(img_data)
    print(f"Сохранено: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Генерация изображений DALL-E 3")
    parser.add_argument("prompt", help="Промт для генерации")
    parser.add_argument("--size", default="1024x1024",
                        help="Размер: 1024x1024, 1792x1024, 1024x1792 или square/landscape/portrait")
    parser.add_argument("--name", help="Имя файла (без расширения)")
    args = parser.parse_args()

    size = SIZES.get(args.size, args.size)
    load_env()
    generate(args.prompt, size, args.name)


if __name__ == "__main__":
    main()
