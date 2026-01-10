from hass_energy.lib.home_assistant import HomeAssistantConfig


class TestHomeAssistantConfig:
    def test_websocket_url_https(self) -> None:
        config = HomeAssistantConfig(
            base_url="https://hass.example.com",
            token="test-token",
        )
        assert config.websocket_url() == "wss://hass.example.com/api/websocket"

    def test_websocket_url_http(self) -> None:
        config = HomeAssistantConfig(
            base_url="http://localhost:8123",
            token="test-token",
        )
        assert config.websocket_url() == "ws://localhost:8123/api/websocket"

    def test_websocket_url_strips_trailing_slash(self) -> None:
        config = HomeAssistantConfig(
            base_url="https://hass.example.com/",
            token="test-token",
        )
        assert config.websocket_url() == "wss://hass.example.com/api/websocket"
