import sys
from importlib.metadata import version

packages = [
    "requests",
    "pandas",
    "pydantic",
    "pytest",
    "openpyxl",
    "fastapi",
    "uvicorn",
    "jinja2",
    "tenacity",
    "openai",
    "pydantic-settings",
    "httpx",
    "responses",
    "Faker",
    "defusedxml",
    "ruff",
]

print("Python   :", sys.version.split()[0])
for name in packages:
    print(f"{name:<17}: {version(name)}")
print("OK: environment ready")
