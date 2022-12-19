#!/usr/bin/python3

from setuptools import setup, find_packages

setup(
    name="k8sh",
    version="0.9.0",
    description="Interactive shell for kubernetes",
    author="Giuseppe Lavagetto",
    author_email="lavagetto@gmail.com",
    url="https://github.com/lavagetto/k8sh",
    install_requires=["cmd2", "pyyaml", "xdg", "colorama"],
    zip_safe=False,
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "k8sh = k8sh.shell:main",
        ],
    },
    classifiers=[
        "Intended Audience :: System Administrators",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: System :: Clustering",
    ],
)
