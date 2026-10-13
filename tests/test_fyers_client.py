import unittest

from src.fyers_client import FyersClient


class ExpiredContractParsingTests(unittest.TestCase):
    def test_reads_nested_options_from_fyers_response(self):
        client = object.__new__(FyersClient)
        client._get = lambda *_: {
            "s": "ok",
            "data": {"contracts": {
                "futures": ["BSE:SENSEX25103FUT"],
                "options": ["BSE:SENSEX2510373200PE", "BSE:SENSEX2510373200CE"],
            }},
        }
        self.assertEqual(
            client.expired_contracts("BSE:SENSEX-INDEX", "2025-01-03"),
            ["BSE:SENSEX2510373200CE", "BSE:SENSEX2510373200PE"],
        )

    def test_allows_successful_expiry_with_no_option_contracts(self):
        client = object.__new__(FyersClient)
        client._get = lambda *_: {
            "s": "ok",
            "data": {"contracts": {"futures": ["BSE:SENSEX25110FUT"], "options": []}},
        }
        self.assertEqual(client.expired_contracts("BSE:SENSEX-INDEX", "2025-01-10"), [])

    def test_parses_sensex_weekly_expiry_strike(self):
        self.assertEqual(
            FyersClient.parse_option_symbol("BSE:SENSEX2510373200PE", "2025-01-03"),
            {"strike": 73200.0, "option_type": "PE"},
        )


if __name__ == "__main__":
    unittest.main()
