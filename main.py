"""
NIFTY OI Tracker - Entry Point
Run with: uv run python main.py
"""

from app import start_app


def main():
    print("Starting NIFTY OI Tracker...")
    print("Open http://localhost:5000 in your browser")
    start_app(debug=True, port=5000)


if __name__ == "__main__":
    main()
