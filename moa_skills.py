#!/usr/bin/env python3
"""
GitHub Skills — скачивание и регистрация скилов для OpenCode.

Использование:
  python moa_skills.py install https://github.com/user/repo
  python moa_skills.py list
  python moa_skills.py sync --all
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional


def _get_opencode_skills_dir() -> Path:
    """Определяем куда класть скилы."""
    # Сначала project-level
    cwd = Path.cwd()
    for d in [cwd / ".opencode" / "skills", cwd / ".opencode" / "skill"]:
        if d.exists():
            return d
    # Создаём
    target = cwd / ".opencode" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    return target


async def install_from_github(repo_url: str, subdir: str = "") -> list[dict]:
    """
    Скачать репу с GitHub, найти SKILL.md, установить в .opencode/skills/.

    Поддерживает: github.com/user/repo, user/repo, https://...git
    """
    # Нормализуем URL
    repo_url = repo_url.rstrip("/")
    if not repo_url.startswith("http"):
        if "/" in repo_url and not repo_url.startswith("github.com"):
            repo_url = f"https://github.com/{repo_url}"
        elif repo_url.startswith("github.com"):
            repo_url = f"https://{repo_url}"

    # Извлекаем user/repo
    match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", repo_url)
    if not match:
        return [{"error": f"Invalid GitHub URL: {repo_url}"}]

    repo_path = match.group(1)
    repo_name = repo_path.split("/")[-1]

    # Скачиваем zip архив репы
    zip_url = f"https://api.github.com/repos/{repo_path}/zipball"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "MoA-Skills/1.0",
    }

    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    print(f"  [SKILLS] Downloading {repo_path}...")

    import httpx

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(zip_url, headers=headers)
        if r.status_code != 200:
            return [{"error": f"GitHub API {r.status_code}: {r.text[:200]}"}]
        data = r.content

    # Распаковываем во временную папку
    tmp_dir = Path(tempfile.mkdtemp(prefix="moa_skills_"))
    zip_path = tmp_dir / "repo.zip"
    zip_path.write_bytes(data)

    extract_dir = tmp_dir / "extracted"
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(str(extract_dir))

    # Ищем SKILL.md в extracted/<repo>-<hash>/
    skill_files = list(extract_dir.rglob("SKILL.md"))
    if not skill_files:
        skill_files = list(extract_dir.rglob("skill.md"))
    if not skill_files:
        shutil.rmtree(tmp_dir)
        return [{"error": "No SKILL.md found in repository"}]

    installed = []
    skills_dir = _get_opencode_skills_dir()

    for skill_path in skill_files:
        rel = skill_path.relative_to(extract_dir)
        # Имя скила = имя папки или имя файла
        skill_name = skill_path.parent.name
        if skill_name == "extracted" or skill_name.startswith(repo_name):
            skill_name = "generic"

        # Читаем SKILL.md для определения имени
        content = skill_path.read_text(encoding="utf-8")
        name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        if name_match:
            skill_name = name_match.group(1).strip()

        # Создаём папку скила
        skill_dir = skills_dir / skill_name
        # Создаём структуру: skills/<name>/SKILL.md  + все файлы из исходной папки
        if skill_dir.exists():
            print(f"  [SKILLS] Updating existing: {skill_name}")
            shutil.rmtree(skill_dir)

        # Копируем всю папку со скилом
        src_dir = skill_path.parent
        shutil.copytree(src_dir, skill_dir, dirs_exist_ok=True)

        # Гарантируем что SKILL.md на месте
        target_skill = skill_dir / "SKILL.md"
        if not target_skill.exists():
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        installed.append({
            "name": skill_name,
            "path": str(skill_dir),
            "files": [str(f.relative_to(skill_dir)) for f in skill_dir.iterdir()],
        })
        print(f"  [SKILLS] Installed: {skill_name} -> {skill_dir}")

    # Чистим временные файлы
    shutil.rmtree(tmp_dir)
    return installed


def list_installed() -> list[dict]:
    """Показать установленные скилы."""
    skills_dir = _get_opencode_skills_dir()
    if not skills_dir.exists():
        return []

    result = []
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir():
            skill_file = d / "SKILL.md"
            desc = ""
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8", errors="ignore")
                desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
                if desc_match:
                    desc = desc_match.group(1).strip()[:100]

            files = [str(f.name) for f in d.iterdir()]
            result.append({
                "name": d.name,
                "path": str(d),
                "description": desc,
                "files": files,
            })
    return result


def uninstall(name: str) -> bool:
    """Удалить скил."""
    skills_dir = _get_opencode_skills_dir()
    target = skills_dir / name
    if target.exists() and target.is_dir():
        shutil.rmtree(target)
        print(f"  [SKILLS] Uninstalled: {name}")
        return True
    print(f"  [SKILLS] Not found: {name}")
    return False


def generate_opencode_config() -> dict:
    """Сгенерировать opencode.json с путями к скилам."""
    skills_dir = _get_opencode_skills_dir()
    if not skills_dir.exists() or not any(skills_dir.iterdir()):
        return {}

    config = {
        "$schema": "https://opencode.ai/config.json",
        "skills": {
            "paths": [str(skills_dir.absolute())],
        },
    }

    # Создаём/обновляем opencode.json
    config_path = Path.cwd() / "opencode.json"
    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            pass

    existing.update(config)
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [SKILLS] Config updated: {config_path}")
    return existing


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="MoA GitHub Skills Manager")
    sub = parser.add_subparsers(dest="cmd")

    install_cmd = sub.add_parser("install", help="Установить скилы с GitHub")
    install_cmd.add_argument("url", help="URL репозитория (user/repo или https://...)")
    install_cmd.add_argument("--subdir", default="", help="Подпапка со скилами")
    install_cmd.add_argument("--no-config", action="store_true", help="Не обновлять opencode.json")

    sub.add_parser("list", help="Список установленных скилов")
    uninstall_cmd = sub.add_parser("uninstall", help="Удалить скил")
    uninstall_cmd.add_argument("name", help="Имя скила")
    sub.add_parser("sync", help="Обновить opencode.json")

    args = parser.parse_args()

    if args.cmd == "install":
        result = await install_from_github(args.url, args.subdir)
        for item in result:
            if "error" in item:
                print(f"  [ERROR] {item['error']}")
            else:
                print(f"  [OK] {item['name']} ({len(item['files'])} files)")

        if not args.no_config and not any("error" in i for i in result):
            generate_opencode_config()

    elif args.cmd == "list":
        skills = list_installed()
        if not skills:
            print("  No skills installed.")
        for s in skills:
            desc = f" - {s['description']}" if s['description'] else ""
            print(f"  {s['name']}{desc}")
            for f in s['files']:
                print(f"    {f}")

    elif args.cmd == "uninstall":
        uninstall(args.name)

    elif args.cmd == "sync":
        generate_opencode_config()

    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
