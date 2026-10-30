"""命令行：init-db 与 doctor。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db as dbmod
from .config import APP_DIR, load_settings
from .service import Service


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = dbmod.connect(settings.db_path)
    seed = Path(args.seed) if args.seed else (APP_DIR.parent / "seed" / "db-systems.cards.json")
    if not seed.exists():
        print(f"种子文件不存在：{seed}", file=sys.stderr)
        return 1
    counts = dbmod.init_db(conn, seed, settings.today())
    print(json.dumps({"db": str(settings.db_path), **counts}, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings()
    service = Service.bootstrap(settings)
    health = service.health()

    rows = [
        ("PowerContext 地址", settings.base_url),
        ("Server 就绪", "是" if health["pc_ready"] else "否（应用会以离线模式运行）"),
        ("检索模式", health.get("memory_search_modes") or "未知（未连上 Server）"),
        ("scope", health["scope_id"] or "未建立"),
        ("卡片数", health["cards"]),
        ("本地记忆指针", health["memory_refs"]),
        ("业务日期", health["date"]),
        ("数据库", health["db_path"]),
        ("审计日志", health["audit_path"]),
    ]
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name.ljust(width)} : {value}")

    if not health["pc_ready"]:
        print("\n提示：先运行 .\\scripts\\start.ps1 -ServerOnly，再重新执行 doctor。", file=sys.stderr)
        return 0 if args.allow_offline else 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recallcoach", description="RecallCoach：数据库系统复习教练")
    sub = parser.add_subparsers(dest="command", required=True)

    init_db = sub.add_parser("init-db", help="建库并导入种子题库")
    init_db.add_argument("--seed", default=None, help="种子 JSON 路径")
    init_db.set_defaults(func=cmd_init_db)

    doctor = sub.add_parser("doctor", help="打印环境与记忆状态")
    doctor.add_argument("--allow-offline", action="store_true", help="Server 未就绪也返回 0")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
