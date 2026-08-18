#[[[ @module sv2ipxact
#]]
#[[[
# Provides the following function:
#
#   sv2ipxact(
#       SV_FILE     <path/to/module.sv>    # input SystemVerilog file
#       META_FILE   <path/to/module.json>  # VLNV metadata (vendor/library[/version])
#       OUTDIR      <path/to/output/dir>   # where the .xml is written
#       [PKG_FILES  <pkg1.sv> …]           # package files referenced by the module
#       [DEFINES    <SYM> …]               # preprocessor symbols for `ifdef resolution
#   )
#
# META_FILE is a small JSON file giving the component's VLNV vendor and
# library (and optionally version, default "1.0") — see sv2ipxact.py's
# docstring for the exact schema. The VLNV "name" always comes from the
# parsed SV module name, not from this file.
#
# The output file is named <module-name>.xml, where <module-name> is derived
# from the SV filename stem (the actual parsed module name is set by the script
# itself inside the XML).
#
# Requirements:
#   - CMake >= 3.19 (find_package(Python3) with Interpreter component; string(JSON ...))
#   - Python 3 with pyslang installed  (pip install pyslang)
#
# The custom target is named:
#   <vendor>_<library>_<stem>_sv2ipxact
# (vendor/library are read from META_FILE at configure time) and is added to
# the ALL target so it always runs during a normal build. The variable
# SV2IPXACT_OUTPUT_FILE is set in the caller's scope to the absolute path of
# the generated XML file.
#]]

set(_SV2IPXACT_DEFAULT_SCRIPT
    "${CMAKE_CURRENT_LIST_DIR}/../sv2ipxact.py"
    CACHE FILEPATH "Default path to sv2ipxact.py"
)

function(sv2ipxact)
    set(options )
    set(oneValueArgs SV_FILE META_FILE OUTDIR)
    set(multiValueArgs PKG_FILES DEFINES)

    cmake_parse_arguments(
        ARG
        "${options}"
        "${oneValueArgs}"
        "${multiValueArgs}"
        ${ARGN}
    )

    foreach(_req SV_FILE META_FILE)
        if(NOT DEFINED ARG_${_req})
            message(FATAL_ERROR "sv2ipxact: missing required argument ${_req}")
        endif()
    endforeach()

    if(NOT ARG_OUTDIR)
        set(ARG_OUTDIR "${PROJECT_BINARY_DIR}/ipxact")
    endif()

    get_filename_component(_sv_abs   "${ARG_SV_FILE}"               ABSOLUTE)
    get_filename_component(_sv_stem  "${ARG_SV_FILE}"               NAME_WE)
    get_filename_component(_meta_abs "${ARG_META_FILE}"             ABSOLUTE)
    get_filename_component(_out_abs  "${ARG_OUTDIR}"                ABSOLUTE)
    get_filename_component(_tool_abs "${_SV2IPXACT_DEFAULT_SCRIPT}" ABSOLUTE)
    get_filename_component(_tool_dir "${_tool_abs}" DIRECTORY)

    # sv2ipxact.py imports it own modules at run time.
    # Modifying any of them should trigger a rebuild.
    file(GLOB _module_deps CONFIGURE_DEPENDS "${_tool_dir}/*.py")

    set(_xml_output "${_out_abs}/${_sv_stem}.xml")

    if(NOT EXISTS "${_sv_abs}")
        message(FATAL_ERROR
            "sv2ipxact: SV_FILE not found:\n  ${_sv_abs}")
    endif()

    if(NOT EXISTS "${_meta_abs}")
        message(FATAL_ERROR
            "sv2ipxact: META_FILE not found:\n  ${_meta_abs}")
    endif()

    if(NOT EXISTS "${_tool_abs}")
        message(FATAL_ERROR
            "sv2ipxact: sv2ipxact.py not found at:  ${_tool_abs}\n"
            "Set _SV2IPXACT_DEFAULT_SCRIPT or place sv2ipxact.py next to sv2ipxact.cmake.")
    endif()

    # Read vendor/library (required) straight out of META_FILE, just for
    # naming the target and the build comment — sv2ipxact.py re-reads the
    # same file at build time and remains the single source of truth.
    file(READ "${_meta_abs}" _meta_json)
    string(JSON _vendor  GET "${_meta_json}" "vendor")
    string(JSON _library GET "${_meta_json}" "library")

    set(_pkg_args)
    set(_pkg_deps)

    if(ARG_PKG_FILES)
        list(APPEND _pkg_args "--pkg")
        foreach(_pkg ${ARG_PKG_FILES})
            get_filename_component(_pkg_abs "${_pkg}" ABSOLUTE)
            if(NOT EXISTS "${_pkg_abs}")
                message(WARNING
                    "sv2ipxact: PKG_FILES entry not found: ${_pkg_abs}")
            endif()
            list(APPEND _pkg_args "${_pkg_abs}")
            list(APPEND _pkg_deps "${_pkg_abs}")
        endforeach()
    endif()

    set(_define_args)

    if(ARG_DEFINES)
        list(APPEND _define_args "--define")
        foreach(_sym ${ARG_DEFINES})
            list(APPEND _define_args "${_sym}")
        endforeach()
    endif()

    file(MAKE_DIRECTORY "${_out_abs}")

    add_custom_command(
        OUTPUT  "${_xml_output}"
        COMMAND "${Python3_EXECUTABLE}"
                    "${_tool_abs}"
                    --input  "${_sv_abs}"
                    --output "${_xml_output}"
                    --meta   "${_meta_abs}"
                    ${_pkg_args}
                    ${_define_args}
        DEPENDS
            "${_sv_abs}"
            "${_meta_abs}"
            ${_module_deps}
            ${_pkg_deps}
        COMMENT
            "[sv2ipxact] ${_sv_stem}.sv → ${_sv_stem}.xml (${_vendor}:${_library}:${_sv_stem})"
        VERBATIM
    )

    add_custom_target("${_vendor}_${_library}_${_sv_stem}_sv2ipxact" ALL
        DEPENDS "${_xml_output}"
    )

    set(SV2IPXACT_OUTPUT_FILE "${_xml_output}" PARENT_SCOPE)

    message(STATUS
        "[sv2ipxact] Registered target "
        "'${_vendor}_${_library}_${_sv_stem}_sv2ipxact': "
        "${_sv_stem}.sv → ${_sv_stem}.xml"
    )
endfunction()
