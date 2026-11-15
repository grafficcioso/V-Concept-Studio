"""V-Concept Studio 실행 스크립트:  python main.py"""
import sys

from crs.app import main

if __name__ == "__main__":
    if "--mcp" in sys.argv:
        from crs.mcp_server import main as mcp_main
        sys.exit(mcp_main())
    sys.exit(main())
