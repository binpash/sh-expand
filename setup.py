from setuptools import setup

from pathlib import Path
long_description = (Path(__file__).parent / "README.md").read_text()

def get_install_requires() -> "list[str]":
    """Returns requirements.txt parsed to a list"""
    fname = Path(__file__).parent / 'requirements.txt'
    targets = []
    if fname.exists():
        with open(fname, 'r') as f:
            targets = f.read().splitlines()
    return targets

setup(name='sh-expand',
      version='0.1.0',
      packages=['sh_expand'],
      ## Necessary for the markdown to be properly rendered
      long_description=long_description,
      long_description_content_type="text/markdown",
      python_requires='>=3.8',
      include_package_data=True,
      install_requires=get_install_requires(),
      )
