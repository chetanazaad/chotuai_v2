"""Package setup for chotu_ai."""
from setuptools import setup, find_packages

setup(
    name="chotu_ai",
    version="1.0.0",
    description="A deterministic, state-driven autonomous execution engine",
    author="chotu_ai",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "fastapi>=0.100.0",
        "uvicorn[standard]>=0.20.0",
    ],
    entry_points={
        "console_scripts": [
            "chotu=chotu_ai.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)