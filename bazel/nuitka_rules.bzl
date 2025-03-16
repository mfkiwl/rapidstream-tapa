"""Custom rule to add Nuitka target to the target list."""

# Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
# All rights reserved. The contributor(s) of this file has/have agreed to the
# RapidStream Contributor License Agreement.

load("@rules_python//python:py_binary.bzl", "py_binary")
load("@rules_python//python:py_executable_info.bzl", "PyExecutableInfo")
load("@tapa_deps//:requirements.bzl", "requirement")

# Define the implementation function for the custom Nuitka rule.
def _nuitka_binary_impl(ctx):
    # Retrieve the inputs and attributes from the rule invocation.
    src = ctx.attr.src[PyExecutableInfo].main
    flags = ctx.attr.flags
    output_dir = ctx.actions.declare_directory(src.basename.split(".")[0] + ".dist")

    # Get the Python toolchain information.
    py_toolchain = ctx.toolchains["@rules_python//python:toolchain_type"].py3_runtime
    py_interpreter = py_toolchain.interpreter.path
    py_bin_dir = py_toolchain.interpreter.dirname
    py_lib_dir = py_toolchain.interpreter.dirname.rsplit("/", 1)[0] + "/lib"

    # Get the Nuitka executable.
    nuitka = ctx.executable.nuitka

    # Start building the command to run Nuitka.
    # By default, caching is enable but discarded due to sandboxing.
    # To preserve cache:
    #   mkdir -p /tmp/.nuitka_cache  # This won't be written, but must exist.
    #   mkdir -p "/var/tmp/${USER}/nuitka-cache"  # This will be written.
    #   echo "build --sandbox_add_mount_pair=/var/tmp/${USER}/nuitka-cache:/tmp/.nuitka_cache" >> ~/.bazelrc
    #   echo "build --sandbox_writable_path=/tmp/.nuitka_cache" >> ~/.bazelrc
    nuitka_cmd = [
        nuitka.path,
        src.path,
        "--clang",  # Use clang as the compiler. In this case, Nuitka generates .c files.
        "--output-dir={}".format(output_dir.dirname),
        "--output-filename={}".format(ctx.attr.output_name or ctx.attr.name),
        "--show-scons",
        "--standalone",
    ]

    # Do not package the generated main file as data.
    for file in ctx.attr.src.files.to_list():
        if not file.is_source:
            nuitka_cmd.append("--noinclude-data-files=" + file.short_path)

    # Add optional flags, if specified.
    if flags:
        nuitka_cmd.extend(flags)

    # Create symlinks to the required tools.
    tools = []  # type: list[File]
    for tool_name, tool in {
        # Please keep sorted.
        "ld": ctx.executable._ld,
        "patchelf": ctx.executable._patchelf,
        "readelf": ctx.executable._readelf,
    }.items():
        if type(tool) == type(""):
            # `tool` is a path string; create symlink to that path verbatim.
            tool_file = ctx.actions.declare_symlink("_nuitka_tools/" + tool_name)
            ctx.actions.symlink(output = tool_file, target_path = tool)
        else:
            # `tool` is a `File`; create symlink with relative path resolved.
            tool_file = ctx.actions.declare_file("_nuitka_tools/" + tool_name)
            ctx.actions.symlink(output = tool_file, target_file = tool, is_executable = True)
        tools.append(tool_file)

    # Add action tools to the env
    env = {
        "PATH": ctx.configuration.host_path_separator.join(
            # Using `depset` for deduplication.
            depset([py_bin_dir] + [x.dirname for x in tools]).to_list() + ["/usr/bin", "/bin"],
        ),
        "LIBRARY_PATH": py_lib_dir,
        "LD_LIBRARY_PATH": py_lib_dir,
        "CC": ctx.executable._clang.path,
        "CCACHE_TEMPDIR": "/tmp/.nuitka_cache/ccache_tmp",
    }

    # Define a custom action to run the Nuitka command.
    tools += [
        # Please keep sorted.
        ctx.executable._clang,
        nuitka,
        py_toolchain.interpreter,
    ]
    ctx.actions.run(
        outputs = [output_dir],
        inputs = depset([src], transitive = [py_toolchain.files]),
        tools = tools,
        executable = py_interpreter,
        arguments = nuitka_cmd,
        mnemonic = "Nuitka",
        env = env,
    )

    # Create the list of all files generated under the output directory.
    # all_files = ctx.actions.glob([output_file.dirname + src.basename + ".dist/**"])

    return [DefaultInfo(files = depset([output_dir]))]

# Define the custom Bazel rule.
_nuitka_binary = rule(
    implementation = _nuitka_binary_impl,
    # Define the attributes (inputs) for the rule.
    attrs = {
        "src": attr.label(
            doc = "The Python binary to compile.",
            mandatory = True,
            providers = [PyExecutableInfo],
        ),
        "output_name": attr.string(
            doc = "The name of the output binary.",
            mandatory = True,
        ),
        "flags": attr.string_list(
            doc = "Flags to pass to Nuitka.",
            default = [],
        ),
        "nuitka": attr.label(
            doc = "The Nuitka executable.",
            mandatory = True,
            executable = True,
            cfg = "exec",
        ),
        "_clang": attr.label(
            doc = "The clang executable.",
            default = Label("@llvm_toolchain_llvm//:bin/clang"),
            executable = True,
            cfg = "exec",
            allow_files = True,
        ),
        "_ld": attr.label(
            doc = "The ld executable.",
            default = Label("@llvm_toolchain_llvm//:bin/ld.lld"),
            executable = True,
            cfg = "exec",
            allow_files = True,
        ),
        "_patchelf": attr.label(
            doc = "The patchelf executable.",
            default = Label("@patchelf"),
            executable = True,
            cfg = "exec",
        ),
        "_readelf": attr.label(
            doc = "The readelf executable.",
            default = Label("@llvm_toolchain_llvm//:bin/llvm-readelf"),
            executable = True,
            cfg = "exec",
            allow_files = True,
        ),
    },
    toolchains = [
        "@rules_python//python:toolchain_type",
    ],
    fragments = ["cpp"],
)

def nuitka_binary(name, src, **kwargs):
    # Generate a Nuitka binary with all Python dependencies.
    py_binary(
        name = name + ".nuitka.py",
        srcs = ["//bazel:nuitka_wrapper.py"],
        main = "nuitka_wrapper.py",
        deps = [src, requirement("nuitka")],
    )
    _nuitka_binary(
        name = name,
        src = src,
        nuitka = name + ".nuitka.py",
        **kwargs
    )
