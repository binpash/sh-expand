from setuptools import setup

from pathlib import Path
long_description = (Path(__file__).parent / "README.md").read_text()

setup(name='sh-expand',
      version='0.1.0',
      packages=['sh_expand'],
      ## Necessary for the markdown to be properly rendered
      long_description=long_description,
      long_description_content_type="text/markdown",
      python_requires='>=3.8',
      include_package_data=True,
      )
