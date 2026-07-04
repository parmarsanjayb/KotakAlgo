from execution.models import ExecutionOrder, OrderProductType

class OrderValidator:
    """
    Validates execution order requests before queuing.
    """
    def validate(self, order: ExecutionOrder) -> tuple[bool, str]:
        # Validate side
        if order.side.upper() not in ("BUY", "SELL"):
            return False, f"Invalid order side: {order.side}"
        
        # Validate quantity
        if order.quantity <= 0:
            return False, f"Quantity must be greater than zero: {order.quantity}"
        
        # Validate order type and required prices
        otype = order.order_type.upper()
        if otype not in ("MARKET", "LIMIT", "SL", "SL-M"):
            return False, f"Unsupported order type: {order.order_type}"
        
        if otype == "LIMIT" and (order.price is None or order.price <= 0):
            return False, "Price must be greater than zero for LIMIT orders"
            
        if otype == "SL":
            if order.price is None or order.price <= 0:
                return False, "Price must be greater than zero for SL orders"
            if order.stop_price is None or order.stop_price <= 0:
                return False, "Stop price must be greater than zero for SL orders"
                
        if otype == "SL-M" and (order.stop_price is None or order.stop_price <= 0):
            return False, "Stop price must be greater than zero for SL-M orders"

        # Validate product type
        if not isinstance(order.product_type, OrderProductType):
            try:
                # Try parsing
                OrderProductType(order.product_type)
            except ValueError:
                return False, f"Unsupported product type: {order.product_type}"

        return True, "VALIDATED"
