if(NOT DEFINED RUNNER OR NOT DEFINED ARTIFACTS OR NOT DEFINED INPUT)
    message(FATAL_ERROR "RUNNER, ARTIFACTS, and INPUT are required")
endif()
execute_process(
    COMMAND "${RUNNER}" --jsonl "${ARTIFACTS}"
    INPUT_FILE "${INPUT}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "composed replay failed (${result}): ${error}\n${output}")
endif()
string(REGEX MATCHALL "\"ok\":true" success_rows "${output}")
list(LENGTH success_rows success_count)
if(NOT success_count EQUAL 6)
    message(FATAL_ERROR "expected 6 successful composed commands, got ${success_count}:\n${output}")
endif()
