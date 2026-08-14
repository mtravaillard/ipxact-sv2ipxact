#[[[ @module sv2ipxact
#]]
#[[[
# Provides the following function:
#
#   sv2ipxact(
#       SV_FILE     <path/to/module.sv>    # input SystemVerilog file
#       OUTDIR      <path/to/output/dir>   # where the .xml is written
#       VENDOR      <vendor-string>        # VLNV vendor
#       LIBRARY     <library-string>       # VLNV library
#       [VERSION    <version-string>]      # VLNV version (default: "1.0")
#       [PKG_FILES  <pkg1.sv> …]           # package files referenced by the module
#       [DEFINES    <SYM> …]               # preprocessor symbols for `ifdef resolution
#   )
#
# The output file is named <module-name>.xml, where <module-name> is derived
# from the SV filename stem (the actual parsed module name is set by the script
# itself inside the XML).
#
# Requirements:
#   - CMake >= 3.18 (find_package(Python3) with Interpreter component)
#   - Python 3 with pyslang installed  (pip install pyslang)
#
# The custom target is named:
#   <VENDOR>_<LIBRARY>_<stem>_sv2ipxact
# and is added to the ALL target so it always runs during a normal build.
# The variable SV2IPXACT_OUTPUT_FILE is set in the caller's scope to the
# absolute path of the generated XML file.
#]]

set(_SV2IPXACT_DEFAULT_SCRIPT
    "${CMAKE_CURRENT_SOURCE_DIR}/../sv2ipxact.py"
    CACHE FILEPATH "Default path to sv2ipxact.py"
)

function(sv2ipxact)
    set(options )
    set(oneValueArgs SV_FILE OUTDIR VENDOR LIBRARY VERSION)
    set(multiValueArgs PKG_FILES DEFINES)

    cmake_parse_arguments(
        ARG
        "${options}"
        "${oneValueArgs}"
        "${multiValueArgs}"
        ${ARGN}
    )

    foreach(_req SV_FILE VENDOR LIBRARY)
        if(NOT DEFINED ARG_${_req})
            message(FATAL_ERROR "sv2ipxact: missing required argument ${_req}")
        endif()
    endforeach()

    if(NOT DEFINED ARG_VERSION)
        set(ARG_VERSION "1.0")
    endif()

    if(NOT ARG_OUTDIR)
        set(ARG_OUTDIR "${PROJECT_BINARY_DIR}/ipxact")
    endif()

    get_filename_component(_sv_abs   "${ARG_SV_FILE}"               ABSOLUTE)
    get_filename_component(_sv_stem  "${ARG_SV_FILE}"               NAME_WE)
    get_filename_component(_out_abs  "${ARG_OUTDIR}"                ABSOLUTE)
    get_filename_component(_tool_abs "${_SV2IPXACT_DEFAULT_SCRIPT}" ABSOLUTE)

    set(_xml_output "${_out_abs}/${_sv_stem}.xml")

    if(NOT EXISTS "${_sv_abs}")
        message(FATAL_ERROR
            "sv2ipxact: SV_FILE not found:\n  ${_sv_abs}")
    endif()

    if(NOT EXISTS "${_tool_abs}")
        message(FATAL_ERROR
            "sv2ipxact: sv2ipxact.py not found at:  ${_tool_abs}\n"
            "Set _SV2IPXACT_DEFAULT_SCRIPT or place sv2ipxact.py next to sv2ipxact.cmake.")
    endif()

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
                    --input   "${_sv_abs}"
                    --output  "${_xml_output}"
                    --vendor  "${ARG_VENDOR}"
                    --library "${ARG_LIBRARY}"
                    --version "${ARG_VERSION}"
                    ${_pkg_args}
                    ${_define_args}
        DEPENDS
            "${_sv_abs}"
            "${_tool_abs}"
            ${_pkg_deps}
        COMMENT
            "[sv2ipxact] ${_sv_stem}.sv → ${_sv_stem}.xml (${ARG_VENDOR}:${ARG_LIBRARY}:${_sv_stem}:${ARG_VERSION})"
        VERBATIM
    )

    add_custom_target("${ARG_VENDOR}_${ARG_LIBRARY}_${_sv_stem}_sv2ipxact" ALL
        DEPENDS "${_xml_output}"
    )

    set(SV2IPXACT_OUTPUT_FILE "${_xml_output}" PARENT_SCOPE)

    message(STATUS
        "[sv2ipxact] Registered target "
        "'${ARG_VENDOR}_${ARG_LIBRARY}_${_sv_stem}_sv2ipxact': "
        "${_sv_stem}.sv → ${_sv_stem}.xml"
    )
endfunction()
