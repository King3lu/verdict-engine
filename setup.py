from setuptools import setup, find_packages

setup(
    name="verdict-engine",
    version="0.1.0",
    description="Open-source multi-source truth verification engine",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="king3lu",
    license="Apache-2.0",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "anthropic>=0.34.0",
        "google-genai>=1.0.0",
        "requests>=2.32.0",
        "httpx>=0.27.0",
    ],
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
