import setuptools

setuptools.setup(
    name="llmpistyle",
    version="0.0.1",
    description="llm pi-mono style toolbox",
    author="Benjamin Sanchez",
    packages=setuptools.find_packages('.'),
    # py_modules=["llmpystyle"],
    # package_dir={"": "."},
    install_requires=[
        "llm",
        ],
    python_requires=">=3.8",
    entry_points={'llm': ["llmpistyle = llmpistyle"]},
    )
