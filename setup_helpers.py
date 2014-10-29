''' Distutils / setuptools helpers

'''
import os
from os.path import join as pjoin, split as psplit, splitext, dirname, exists
import tempfile
import shutil

from distutils.command.install_scripts import install_scripts
from distutils.errors import CompileError, LinkError

from distutils import log

BAT_TEMPLATE = \
r"""@echo off
REM wrapper to use shebang first line of {FNAME}
set mypath=%~dp0
set pyscript="%mypath%{FNAME}"
set /p line1=<%pyscript%
if "%line1:~0,2%" == "#!" (goto :goodstart)
echo First line of %pyscript% does not start with "#!"
exit /b 1
:goodstart
set py_exe=%line1:~2%
REM quote exe in case of spaces in path name
set py_exe="%py_exe%"
call %py_exe% %pyscript% %*
"""

# File to which to write Cython conditional DEF vars
CONFIG_PXI = pjoin('build', 'config.pxi')


class install_scripts_bat(install_scripts):
    """ Make scripts executable on Windows

    Scripts are bare file names without extension on Unix, fitting (for example)
    Debian rules. They identify as python scripts with the usual ``#!`` first
    line. Unix recognizes and uses this first "shebang" line, but Windows does
    not. So, on Windows only we add a ``.bat`` wrapper of name
    ``bare_script_name.bat`` to call ``bare_script_name`` using the python
    interpreter from the #! first line of the script.

    Notes
    -----
    See discussion at
    http://matthew-brett.github.com/pydagogue/installing_scripts.html and
    example at git://github.com/matthew-brett/myscripter.git for more
    background.
    """
    def run(self):
        install_scripts.run(self)
        if not os.name == "nt":
            return
        for filepath in self.get_outputs():
            # If we can find an executable name in the #! top line of the script
            # file, make .bat wrapper for script.
            with open(filepath, 'rt') as fobj:
                first_line = fobj.readline()
            if not (first_line.startswith('#!') and
                    'python' in first_line.lower()):
                log.info("No #!python executable found, skipping .bat "
                            "wrapper")
                continue
            pth, fname = psplit(filepath)
            froot, ext = splitext(fname)
            bat_file = pjoin(pth, froot + '.bat')
            bat_contents = BAT_TEMPLATE.replace('{FNAME}', fname)
            log.info("Making %s wrapper for %s" % (bat_file, filepath))
            if self.dry_run:
                continue
            with open(bat_file, 'wt') as fobj:
                fobj.write(bat_contents)


def add_flag_checking(build_ext_class, flag_defines):
    """ Override input `build_ext_class` to check compiler `flag_defines`

    Parameters
    ----------
    build_ext_class : class
        Class implementing ``distutils.command.build_ext.build_ext`` interface,
        with a ``build_extensions`` method.
    flag_defines : sequence
        A sequence of elements, where the elements are sequences of length 4
        consisting of (``compile_flags``, ``link_flags``, ``code``,
        ``defvar``). ``compile_flags`` is a sequence of compiler flags;
        ``link_flags`` is a sequence of linker flags. We
        check ``compile_flags`` to see whether a C source string ``code`` will
        compile, and ``link_flags`` to see whether the resulting object file
        will link.  If both compile and link works, we add ``compile_flags`` to
        ``extra_compile_args`` and ``link_flags`` to ``extra_link_args`` of
        each extension when we build the extensions.  If ``defvar`` is not
        None, it is the name of Cython variable to be defined in
        ``build/config.pxi`` with True if the combination of ``flags``,
        ``code`` will compile, False otherwise. If None, do not write variable.

    Returns
    -------
    checker_class : class
        A class with similar interface to
        ``distutils.command.build_ext.build_ext``, that adds all working
        `input_flag` values to the ``extra_compile_args`` and
        ``extra_link_args`` attributes of extensions, before compiling.
    """
    class Checker(build_ext_class):
        flag_defs = tuple(flag_defines)

        def can_compile_link(self, compile_flags, link_flags, code):
            cc = self.compiler
            fname = 'test.c'
            cwd = os.getcwd()
            tmpdir = tempfile.mkdtemp()
            try:
                os.chdir(tmpdir)
                with open(fname, 'wt') as fobj:
                    fobj.write(code)
                try:
                    objects = cc.compile([fname],
                                         extra_postargs=compile_flags)
                except CompileError:
                    return False
                try:
                    cc.link_executable(objects, "a.out",
                                       extra_postargs=link_flags)
                except (LinkError, TypeError):
                    return False
            finally:
                os.chdir(cwd)
                shutil.rmtree(tmpdir)
            return True

        def build_extensions(self):
            """ Hook into extension building to check compiler flags """
            def_vars = []
            good_compile_flags = []
            good_link_flags = []
            config_dir = dirname(CONFIG_PXI)
            for compile_flags, link_flags, code, def_var in self.flag_defs:
                compile_flags = list(compile_flags)
                link_flags = list(link_flags)
                flags_good = self.can_compile_link(compile_flags,
                                                   link_flags,
                                                   code)
                if def_var:
                    def_vars.append('DEF {0} = {1}'.format(
                        def_var, flags_good))
                if flags_good:
                    good_compile_flags += compile_flags
                    good_link_flags += link_flags
                else:
                    log.warn("Flags {0} omitted because of compile or link "
                             "error".format(compile_flags + link_flags))
            if def_vars:
                if not exists(config_dir):
                    os.mkdir(config_dir)
                with open(CONFIG_PXI, 'wt') as fobj:
                    fobj.write('# Automatically generated; do not edit\n')
                    fobj.write('\n'.join(def_vars))
            if def_vars or good_compile_flags or good_link_flags:
                for ext in self.extensions:
                    ext.extra_compile_args += good_compile_flags
                    ext.extra_link_args += good_link_flags
                    if def_vars:
                        ext.include_dirs.append(config_dir)
            build_ext_class.build_extensions(self)

    return Checker
