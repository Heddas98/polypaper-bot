"""Quick verification: checks Python version and all required packages."""
import sys, importlib, os

results = []
results.append(f"Python: {sys.version}")
results.append(f"Platform: {sys.platform}")
results.append(f"CWD: {os.getcwd()}")
results.append("")

packages = {
    "telegram": "python-telegram-bot",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "apscheduler": "APScheduler",
    "pandas": "pandas",
    "numpy": "numpy",
    "PIL": "Pillow",
    "py_clob_client": "py-clob-client",
    "dotenv": "python-dotenv",
    "yaml": "pyyaml",
    "websockets": "websockets",
    "aiohttp": "aiohttp",
}

ok = 0
fail = 0
for module, package in packages.items():
    try:
        m = importlib.import_module(module)
        ver = getattr(m, "__version__", getattr(m, "VERSION", "?"))
        results.append(f"  OK  {package:25s} (v{ver})")
        ok += 1
    except ImportError as e:
        results.append(f"  FAIL {package:25s} -> {e}")
        fail += 1

results.append("")
results.append(f"Result: {ok} OK, {fail} FAILED")

# Check .env
if os.path.exists(".env"):
    results.append(".env file: FOUND")
else:
    results.append(".env file: NOT FOUND")

output = "\n".join(results)
print(output)

with open("data_store/verify_result.txt", "w", encoding="utf-8") as f:
    f.write(output)

print(f"\nSaved to data_store/verify_result.txt")
input("Press Enter to close...")
