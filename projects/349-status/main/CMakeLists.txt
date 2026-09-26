idf_component_register(
    SRCS "main.c" "link.c" "proto.c" "state.c" "ui.c" "ui_fonts.c" "ui_deck.c" "deck.c" "deck_input.c" "group_input.c" "dashboard.c" "rtc.c"
         "fonts/status_symbol_14.c" "fonts/status_symbol_16.c"
         "fonts/status_text_20.c" "fonts/status_text_22.c"
         "fonts/status_clock_80.c"
    REQUIRES lvgl display_349 json esp_app_format esp_driver_usb_serial_jtag esp_driver_i2c
    INCLUDE_DIRS ".")
