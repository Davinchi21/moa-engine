#!/usr/bin/env python3
"""
MoA Engine — MCP Server for OpenCode integration.

Запуск:
  python moa_mcp_server.py

В opencode.json:
  "mcp": {
    "moa-engine": {
      "type": "local",
      "command": ["python", "путь/к/moa_mcp_server.py"],
      "enabled": true
    }
  }
"""

import asyncio
import os
import sys
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from moa_engine import MoAEngine, Config

engine: MoAEngine | None = None


def get_engine() -> MoAEngine:
    global engine
    if engine is None:
        config_path = Path(__file__).parent / "moa_config.yaml"
        engine = MoAEngine(str(config_path))
    return engine


try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("moa-engine", log_level="WARNING")

    @mcp.tool(
        name="moa_query",
        description="MoA: ансамбль LLM (3 слоя) для max качества. Использует бесплатные OpenRouter модели.",
    )
    async def moa_query(prompt: str) -> str:
        """Полный MoA пайплайн: Proposers → Aggregators → Final."""
        eng = get_engine()
        result = await eng.run(prompt)
        return result.final_answer

    @mcp.tool(
        name="moa_status",
        description="Статус MoA: какие API ключи и модели настроены.",
    )
    async def moa_status() -> str:
        eng = get_engine()
        cfg = eng.cfg
        lines = ["## MoA Engine Status\n"]
        lines.append("### API Keys")
        for p in cfg.proposers + cfg.aggregators_l1 + [cfg.final]:
            key = os.getenv(p.key_env, "")
            status = "OK" if key else "MISSING"
            masked = key[:6] + "..." + key[-4:] if len(key) > 12 else "(empty)"
            lines.append(f"  [{status}] {p.key_env}: {masked}")
        lines.append(f"\n### Proposers: {len(cfg.proposers)}")
        for p in cfg.proposers:
            lines.append(f"  - {p.model}")
        lines.append(f"\n### Aggregators L1: {len(cfg.aggregators_l1)}")
        for a in cfg.aggregators_l1:
            lines.append(f"  - {a.model}")
        lines.append(f"\n### Final: {cfg.final.model}")
        lines.append(f"\n### Scoring: {'on' if cfg.quality_cfg.get('enabled') else 'off'}")
        return "\n".join(lines)

    @mcp.tool(
        name="skills_install",
        description="Установить скилы для OpenCode из GitHub репозитория.",
    )
    async def skills_install(repo_url: str) -> str:
        """Скачать репу с GitHub и установить SKILL.md в .opencode/skills/"""
        from moa_skills import install_from_github, generate_opencode_config, list_installed
        result = await install_from_github(repo_url)
        lines = []
        for item in result:
            if "error" in item:
                lines.append(f"ERROR: {item['error']}")
            else:
                lines.append(f"OK: {item['name']} ({item['path']})")
        generate_opencode_config()
        return "\n".join(lines) if lines else "No skills found."

    @mcp.tool(
        name="skills_list",
        description="Список установленных OpenCode скилов.",
    )
    async def skills_list() -> str:
        from moa_skills import list_installed
        skills = list_installed()
        if not skills:
            return "No skills installed."
        lines = ["## Installed Skills"]
        for s in skills:
            desc = f" - {s['description']}" if s['description'] else ""
            lines.append(f"  {s['name']}{desc}")
        return "\n".join(lines)

    @mcp.tool(
        name="skills_uninstall",
        description="Удалить установленный скил по имени.",
    )
    async def skills_uninstall(name: str) -> str:
        from moa_skills import uninstall
        ok = uninstall(name)
        return f"Uninstalled: {name}" if ok else f"Not found: {name}"

    def main():
        mcp.run()

except ImportError:
    async def cli():
        prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Prompt: ")
        eng = get_engine()
        result = await eng.run(prompt)
        print(result.final_answer)
        print(f"\n---\nModels: {len(result.models_used)} | Latency: {result.latency:.1f}s", file=sys.stderr)

    def main():
        asyncio.run(cli())


if __name__ == "__main__":
    main()
