# SEER Recipe library.
#
# A Recipe is exposed as one VDA5050 instant action while its steps execute in
# order inside the unchanged Adapter action registry. ${var.NAME} creates an
# input field with NAME on the WebUI Actions page.

recipe "seerNavigateToPoint" {
  label               = "SEER - 지정 포인트 이동"
  enabled             = true
  motion              = true
  timeout_sec         = 300
  cleanup_timeout_sec = 5

  step "seerPathNav" {
    parameters = {
      id              = "${var.target_id}"
      source_id       = "SELF_POSITION"
      navigation_mode = "path"
    }
    timeout_sec = 295
  }
}

recipe "seerPulseDO" {
  label               = "SEER - DO 펄스"
  enabled             = true
  motion              = false
  timeout_sec         = 15
  cleanup_timeout_sec = 5

  step "seerSetDO" {
    parameters = {
      id     = "${var.output_id}"
      status = "on"
    }
    timeout_sec = 5
    delay_sec   = 1
  }

  cleanup "seerSetDO" {
    parameters = {
      id     = "${var.output_id}"
      status = "off"
    }
    timeout_sec = 5
  }
}

