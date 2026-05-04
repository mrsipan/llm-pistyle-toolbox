import setuptools

setuptools.setup(
    name="llmpistyletoolbox",
    version="0.0.1",
    description="llm pi-mono style toolbox",
    author="Benjamin Sanchez",
    py_modules=["llmpystyle"],
    package_dir={"": "."},
    install_requires=[
        "llm",
        ],
    python_requires=">=3.8",
    )
