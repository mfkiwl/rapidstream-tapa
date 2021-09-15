import os.path
import os
import subprocess
import sys
import shutil

project = 'TAPA'
author = ', '.join([
    'Yuze Chi',
])
copyright = '2021, ' + author

html_theme = 'sphinx_rtd_theme'

extensions = [
    'breathe',  # Handles Doxygen output.
    'sphinx.ext.autodoc',  # Handles Python docstring.
    'sphinx.ext.napoleon',  # Handles Google-stype docstring.
    'sphinxarg.ext',  # Handles Python entry points.
]

breathe_default_project = 'TAPA'

if os.environ.get('READTHEDOCS') == 'True':
  docs_dir = os.path.dirname(__file__)
  build_dir = docs_dir + '/../build/docs'
  os.makedirs(build_dir, exist_ok=True)
  with open(f'{docs_dir}/Doxyfile.in', 'r') as doxyfile_in:
    with open(f'{build_dir}/Doxyfile', 'w') as doxyfile_out:
      for line in doxyfile_in:
        line = line.replace('@DOXYGEN_OUTPUT_DIR@', f'{build_dir}/doxygen')
        line = line.replace('@DOXYGEN_INPUT_DIR@', f'{docs_dir}/..')
        doxyfile_out.write(line)
  subprocess.call(['doxygen', f'{build_dir}/Doxyfile'])
  breathe_projects = {breathe_default_project: f'{build_dir}/doxygen/xml'}

# Make sure to use the tapa package shipped with the documentation.
sys.path.insert(0, os.path.dirname(__file__) + '/../backend/python')
