from setuptools import setup, find_packages

setup(
    name="vnai",
    version="2.6.3",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "cffi",
        "pandas",
        "psutil",
        "requests",
    ],
)