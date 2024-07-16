class SettingsMapper:
    forward_map = {
        "base_sample_rate_hz": 0,
        "snippet_interval_ms": 1,
        "snippet_length": 2,
        "health_interval_ms": 3,
        "base_axis_enable": 4,
        "motion_threshold_rms_g": 5,
        "motion_standby_delay_ms": 6,
        "wom_sample_rate_hz": 7,
        "wom_threshold_g": 8,
        "snippet_mode": 9,
    }

    reverse_map = dict(reversed(item) for item in forward_map.items())

    @staticmethod
    def from_readable(settings):
        def map_key(key):
            try:
                return SettingsMapper.forward_map[key]
            except KeyError:
                try:
                    return int(key)
                except:
                    raise ValueError(f"Invalid key {key}")

        return {map_key(k): v for k, v in settings.items()}

    @staticmethod
    def to_readable(settings):
        def map_key(key):
            try:
                return SettingsMapper.reverse_map[key]
            except KeyError:
                return str(key)

        return {map_key(k): v for k, v in settings.items()}

