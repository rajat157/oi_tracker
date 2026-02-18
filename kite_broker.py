"""
Kite Connect broker integration for Iron Pulse.
Places orders and GTTs using daily access token.
"""
import os
import requests
from datetime import datetime
from logger import get_logger
from kite_auth import load_token

log = get_logger("kite_broker")

API_KEY = os.environ.get('KITE_API_KEY', '')
BASE_URL = "https://api.kite.trade"


def round_to_tick(price: float, direction: str = "nearest") -> float:
    """Round price to nearest 0.5 (e.g., 86.2 -> 86.0 or 86.5).
    direction: 'up' for targets, 'down' for SL, 'nearest' for entry.
    """
    import math
    if direction == "up":
        return math.ceil(price * 2) / 2  # Round up to 0.5
    elif direction == "down":
        return math.floor(price * 2) / 2  # Round down to 0.5
    return round(price * 2) / 2  # Nearest 0.5


def _headers():
    """Get auth headers with today's access token."""
    token = load_token()
    if not token:
        return None
    return {
        'X-Kite-Version': '3',
        'Authorization': f'token {API_KEY}:{token}'
    }


def place_order(trading_symbol: str, transaction_type: str = "BUY",
                quantity: int = 65, price: float = 0,
                order_type: str = "LIMIT", product: str = "NRML") -> dict:
    """
    Place an order on Kite.
    
    Returns: {"status": "success", "data": {"order_id": "..."}} or error
    """
    headers = _headers()
    if not headers:
        log.error("No Kite access token - run kite_auth.py first")
        return {"status": "error", "message": "No access token"}
    
    data = {
        'tradingsymbol': trading_symbol,
        'exchange': 'NFO',
        'transaction_type': transaction_type,
        'order_type': order_type,
        'quantity': quantity,
        'product': product,
        'validity': 'DAY',
    }
    if order_type == 'LIMIT' and price > 0:
        data['price'] = price
    
    try:
        resp = requests.post(f"{BASE_URL}/orders/regular", headers=headers, data=data, timeout=10)
        result = resp.json()
        
        if result.get('status') == 'success':
            log.info("Order placed", order_id=result['data']['order_id'],
                     symbol=trading_symbol, type=transaction_type, qty=quantity, price=price)
        else:
            log.error("Order failed", result=result, symbol=trading_symbol)
        
        return result
    except Exception as e:
        log.error("Order placement error", error=str(e))
        return {"status": "error", "message": str(e)}


def place_gtt_oco(trading_symbol: str, entry_price: float,
                  sl_price: float, target_price: float,
                  quantity: int = 65, product: str = "NRML") -> dict:
    """
    Place a GTT OCO (One Cancels Other) order.
    Triggers at SL or Target, whichever hits first.
    
    Returns: {"status": "success", "data": {"trigger_id": ...}} or error
    """
    headers = _headers()
    if not headers:
        log.error("No Kite access token - run kite_auth.py first")
        return {"status": "error", "message": "No access token"}
    
    import json
    
    condition = json.dumps({
        "exchange": "NFO",
        "tradingsymbol": trading_symbol,
        "trigger_values": [sl_price, target_price],
        "last_price": entry_price
    })
    
    orders = json.dumps([
        {
            "exchange": "NFO",
            "tradingsymbol": trading_symbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": sl_price
        },
        {
            "exchange": "NFO",
            "tradingsymbol": trading_symbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": target_price
        }
    ])
    
    try:
        resp = requests.post(f"{BASE_URL}/gtt/triggers", headers=headers, data={
            'type': 'two-leg',
            'condition': condition,
            'orders': orders
        }, timeout=10)
        result = resp.json()
        
        if result.get('status') == 'success':
            log.info("GTT OCO placed", trigger_id=result['data']['trigger_id'],
                     symbol=trading_symbol, sl=sl_price, target=target_price)
        else:
            log.error("GTT failed", result=result)
        
        return result
    except Exception as e:
        log.error("GTT placement error", error=str(e))
        return {"status": "error", "message": str(e)}


def modify_gtt(trigger_id: int, trading_symbol: str, current_price: float,
               new_sl_price: float, target_price: float,
               quantity: int = 65, product: str = "NRML") -> dict:
    """Modify an existing GTT (e.g., update trailing SL)."""
    headers = _headers()
    if not headers:
        return {"status": "error", "message": "No access token"}
    
    import json
    
    condition = json.dumps({
        "exchange": "NFO",
        "tradingsymbol": trading_symbol,
        "trigger_values": [new_sl_price, target_price],
        "last_price": current_price
    })
    
    orders = json.dumps([
        {
            "exchange": "NFO",
            "tradingsymbol": trading_symbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": new_sl_price
        },
        {
            "exchange": "NFO",
            "tradingsymbol": trading_symbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": target_price
        }
    ])
    
    try:
        resp = requests.put(f"{BASE_URL}/gtt/triggers/{trigger_id}", headers=headers, data={
            'type': 'two-leg',
            'condition': condition,
            'orders': orders
        }, timeout=10)
        result = resp.json()
        
        if result.get('status') == 'success':
            log.info("GTT modified", trigger_id=trigger_id, new_sl=new_sl_price)
        else:
            log.error("GTT modify failed", result=result)
        
        return result
    except Exception as e:
        log.error("GTT modify error", error=str(e))
        return {"status": "error", "message": str(e)}


def delete_gtt(trigger_id: int) -> dict:
    """Delete a GTT trigger."""
    headers = _headers()
    if not headers:
        return {"status": "error", "message": "No access token"}
    
    try:
        resp = requests.delete(f"{BASE_URL}/gtt/triggers/{trigger_id}", headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def is_authenticated() -> bool:
    """Check if we have a valid access token for today."""
    return bool(load_token())


def auto_place_iron_pulse(trading_symbol: str, entry_premium: float,
                           sl_premium: float, target_premium: float,
                           quantity: int = 65) -> dict:
    """
    Auto-place Iron Pulse trade: LIMIT BUY + GTT OCO (SL + Target).
    Returns dict with order_id and trigger_id, or error.
    """
    if not is_authenticated():
        log.warning("Kite not authenticated - skipping auto order")
        return {"status": "error", "message": "Not authenticated"}
    
    # Round prices to tick size (0.5)
    entry = round_to_tick(entry_premium, "nearest")
    sl = round_to_tick(sl_premium, "down")
    target = round_to_tick(target_premium, "up")
    
    log.info("Auto-placing Iron Pulse", symbol=trading_symbol,
             entry=entry, sl=sl, target=target, qty=quantity)
    
    # 1. Place LIMIT BUY order
    order_result = place_order(
        trading_symbol=trading_symbol,
        transaction_type="BUY",
        quantity=quantity,
        price=entry,
        order_type="LIMIT",
        product="NRML"
    )
    
    if order_result.get('status') != 'success':
        log.error("Failed to place buy order", result=order_result)
        return {"status": "error", "message": f"Order failed: {order_result}", "order": order_result}
    
    order_id = order_result['data']['order_id']
    
    # 2. Place GTT OCO (SL + Target)
    gtt_result = place_gtt_oco(
        trading_symbol=trading_symbol,
        entry_price=entry,
        sl_price=sl,
        target_price=target,
        quantity=quantity,
        product="NRML"
    )
    
    trigger_id = None
    if gtt_result.get('status') == 'success':
        trigger_id = gtt_result['data']['trigger_id']
    else:
        log.error("Failed to place GTT, order is live without SL!", result=gtt_result)
    
    return {
        "status": "success",
        "order_id": order_id,
        "trigger_id": trigger_id,
        "entry": entry,
        "sl": sl,
        "target": target,
        "symbol": trading_symbol
    }
