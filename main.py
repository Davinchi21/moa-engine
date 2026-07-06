#!/usr/bin/env python3
"""
MoA System — Entry Point
~~~~~~~~~~~~~~~~~~~~~~~~~
CLI:    python main.py "твой запрос"
Web UI: python main.py --web
MCP:    python main.py --mcp  (для интеграции с OpenCode)
Skills: python main.py --skills-install user/repo
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Загружаем .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from moa_engine import MoAEngine


def main():
    parser = argparse.ArgumentParser(description="MoA — Mixture of Agents Engine")
    parser.add_argument("prompt", nargs="*", help="Текст запроса")
    parser.add_argument("--web", action="store_true", help="Запустить Web UI (Gradio)")
    parser.add_argument("--mcp", action="store_true", help="Запустить MCP-сервер для OpenCode")
    parser.add_argument("--config", default="moa_config.yaml", help="Путь к конфигу")
    parser.add_argument("--quick", action="store_true", help="Быстрый режим (1 proposer)")
    parser.add_argument("--status", action="store_true", help="Проверить статус API ключей")

    # Skills
    parser.add_argument("--skills-install", metavar="URL", help="Установить скилы из GitHub")
    parser.add_argument("--skills-list", action="store_true", help="Список установленных скилов")
    parser.add_argument("--skills-uninstall", metavar="NAME", help="Удалить скил")

    args = parser.parse_args()

    # ─── Skills ───
    if args.skills_install:
        from moa_skills import install_from_github, generate_opencode_config
        result = asyncio.run(install_from_github(args.skills_install))
        for item in result:
            if "error" in item:
                print(f"ERROR: {item['error']}")
            else:
                print(f"OK: {item['name']}")
        generate_opencode_config()
        return

    if args.skills_list:
        from moa_skills import list_installed
        for s in list_installed():
            desc = f" - {s['description']}" if s['description'] else ""
            print(f"  {s['name']}{desc}")
        return

    if args.skills_uninstall:
        from moa_skills import uninstall
        uninstall(args.skills_uninstall)
        return

    # ─── MCP Server ───
    if args.mcp:
        from moa_mcp_server import main as mcp_main
        mcp_main()
        return

    # ─── Web UI ───
    if args.web:
        run_web(args.config)
        return

    # ─── Status ───
    if args.status:
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass
        import os
        from moa_engine import Config
        cfg = Config(args.config)
        print("=" * 50)
        print("MoA Engine — Status")
        print("=" * 50)
        for p in cfg.proposers + cfg.aggregators_l1 + [cfg.final]:
            key = os.getenv(p.key_env, "")
            icon = "OK" if key else "--"
            masked = key[:6] + "..." + key[-4:] if len(key) > 12 else "(not set)"
            print(f"  [{icon}] {p.key_env}: {masked}")
        print(f"\nProposers: {len(cfg.proposers)}")
        print(f"Aggregators L1: {len(cfg.aggregators_l1)}")
        print(f"Final: {cfg.final.name}")
        print("=" * 50)
        return

    # ─── CLI ───
    prompt = " ".join(args.prompt) if args.prompt else input("Prompt: ")
    asyncio.run(run_cli(prompt, args.config, args.quick))


async def run_cli(prompt: str, config_path: str, quick: bool = False):
    engine = MoAEngine(config_path)
    if quick:
        engine.proposers = engine.proposers[:1]
        engine.aggregators_l1 = []
    result = await engine.run(prompt)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(result.final_answer)
    print(f"\n---\nModels used: {len(result.models_used)} | Time: {result.latency:.1f}s", file=sys.stderr)


def run_web(config_path: str):
    try:
        import gradio as gr
    except ImportError:
        print("Install gradio: pip install gradio")
        sys.exit(1)

    import concurrent.futures
    engine = MoAEngine(config_path)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def sync_process(prompt: str) -> str:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(engine.run(prompt))
            loop.close()
            if result.final_answer.startswith("MoA Error"):
                return f"**Ошибка**: {result.final_answer}\n\nВозможные причины:\n- Достигнут дневной лимит OpenRouter (50 запросов/день). Добавьте $10 кредита для 1000 запросов/день\n- Модели временно недоступны\n- Попробуйте позже"
            models = ", ".join(result.models_used)
            return f"{result.final_answer}\n\n---\nModels: {models} | Time: {result.latency:.1f}s"
        except Exception as e:
            return f"**Ошибка**: {e}"

    demo = gr.Blocks(title="MoA Engine")
    with demo:
        gr.Markdown("# MoA — Mixture of Agents Engine")
        gr.Markdown("3 levels: Proposers -> Aggregators -> Final")
        prompt = gr.Textbox(label="Prompt", lines=4, placeholder="Enter your question...")
        btn = gr.Button("Run MoA", variant="primary")
        output = gr.Markdown(label="Answer")
        btn.click(sync_process, inputs=[prompt], outputs=output, api_name=False)

    demo.launch(server_name="127.0.0.1", server_port=7889, share=False, show_error=True)


if __name__ == "__main__":
    main()
