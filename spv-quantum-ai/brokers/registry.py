class BrokerRegistry:
    """Catalogue of all supported broker adapters."""
    _registry = {
        "paper_broker":        "brokers.paper.PaperBroker",
        "kotak_neo":           "brokers.kotak_neo.KotakNeoAdapter",
        "zerodha":             "brokers.zerodha.ZerodhaAdapter",
        "zerodha_kite":        "brokers.zerodha.ZerodhaAdapter",
        "angel_one":           "brokers.angel_one.AngelOneBrokerAdapter",
        "upstox":              "brokers.upstox.UpstoxAdapter",
        "dhan":                "brokers.dhan.DhanAdapter",
        "interactive_brokers": "brokers.interactive_brokers.InteractiveBrokersAdapter",
        "ibkr":                "brokers.interactive_brokers.InteractiveBrokersAdapter",
        "fyers":               "brokers.kotak_neo.KotakNeoAdapter",
        "shoonya":             "brokers.kotak_neo.KotakNeoAdapter",
        "alice_blue":          "brokers.kotak_neo.KotakNeoAdapter",
    }

    @classmethod
    def get_class_path(cls, name: str) -> str:
        if name not in cls._registry:
            raise ValueError(f"Broker '{name}' is not registered in BrokerRegistry.")
        return cls._registry[name]

    @classmethod
    def get_registered_brokers(cls) -> list[str]:
        return list(cls._registry.keys())

# Maintain backward compatibility
BROKER_REGISTRY = BrokerRegistry._registry
