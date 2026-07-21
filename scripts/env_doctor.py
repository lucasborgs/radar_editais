#!/usr/bin/env python3
"""Diagnóstico somente leitura da identidade do ambiente e do banco."""

from __future__ import annotations

import argparse
import json

from radar.core.environment import load_environment_profile, redacted_environment_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emite JSON para automação.")
    args = parser.parse_args()
    loaded_profile = load_environment_profile()
    report = redacted_environment_report()
    report["loaded_profile"] = str(loaded_profile) if loaded_profile else None
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"environment: {report['environment']}")
        print(f"loaded_profile: {report['loaded_profile'] or 'shell only'}")
        print(f"target: {report['target']}")
        print(f"database_host: {report['database_host'] or '-'}")
        print(f"supabase_host: {report['supabase_host'] or '-'}")
        print(f"project_ref: {report['project_ref'] or '-'}")
        print(f"credentials_present: {report['credentials']}")
        if report.get("sentinel"):
            print(f"sentinel: {report['sentinel']}")
        else:
            print(f"sentinel: ERROR — {report.get('sentinel_error')}")
        print(f"valid: {report['valid']}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
