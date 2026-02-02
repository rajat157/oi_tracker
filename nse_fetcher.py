"""
NSE Option Chain Data Fetcher
Uses Selenium to fetch data from NSE option chain page
"""

import time
import json
from typing import Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


class NSEFetcher:
    """Fetches option chain data from NSE using Selenium."""

    OPTION_CHAIN_URL = "https://www.nseindia.com/option-chain"

    def __init__(self, headless: bool = True):
        """
        Initialize the fetcher.

        Args:
            headless: Run browser in headless mode (no GUI)
        """
        self.headless = headless
        self.driver = None

    def _init_driver(self):
        """Initialize Chrome WebDriver."""
        if self.driver is not None:
            return

        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless=new")

        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        # Remove webdriver flag
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def close(self):
        """Close the browser."""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def fetch_option_chain(self) -> Optional[dict]:
        """
        Fetch NIFTY option chain data from NSE.

        Returns:
            dict with option chain data or None if fetch fails
        """
        try:
            self._init_driver()

            print("Loading NSE option chain page...")
            self.driver.get(self.OPTION_CHAIN_URL)

            # Wait for the table to load
            wait = WebDriverWait(self.driver, 30)

            # Wait for the option chain table to be present
            wait.until(EC.presence_of_element_located((By.ID, "optionChainTable-indices")))

            # Additional wait for data to populate
            time.sleep(3)

            # Extract spot price
            spot_price = self._extract_spot_price()

            # Extract expiry date
            expiry_date = self._extract_expiry_date()

            # Extract strike data from table
            strikes_data = self._extract_table_data()

            if not strikes_data:
                print("No strike data found")
                return None

            return {
                "records": {
                    "underlyingValue": spot_price,
                    "expiryDates": [expiry_date] if expiry_date else [],
                    "data": strikes_data
                }
            }

        except Exception as e:
            print(f"Error fetching option chain: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_spot_price(self) -> Optional[float]:
        """Extract the current spot price from the page."""
        try:
            # Look for the underlying value display
            spot_elem = self.driver.find_element(By.ID, "equity_underlyingVal")
            spot_text = spot_elem.text.strip()
            # Parse "NIFTY 24,150.25" format - extract just the number
            import re
            numbers = re.findall(r'[\d,]+\.?\d*', spot_text)
            if numbers:
                spot_price = float(numbers[-1].replace(",", ""))
                print(f"Spot price: {spot_price}")
                return spot_price
            return None
        except Exception as e:
            print(f"Error extracting spot price: {e}")
            # Try alternative method
            try:
                spot_elem = self.driver.find_element(By.CSS_SELECTOR, "#equity_underlyingVal")
                spot_text = spot_elem.text.strip()
                import re
                numbers = re.findall(r'[\d,]+\.?\d*', spot_text)
                if numbers:
                    return float(numbers[-1].replace(",", ""))
            except:
                pass
            return None

    def _extract_expiry_date(self) -> Optional[str]:
        """Extract the selected expiry date."""
        try:
            expiry_elem = self.driver.find_element(By.ID, "expirySelect")
            expiry_date = expiry_elem.get_attribute("value")
            return expiry_date
        except Exception as e:
            print(f"Error extracting expiry date: {e}")
            return None

    def _extract_atm_strike(self) -> Optional[int]:
        """Extract the ATM strike from the highlighted row (intersection of quadrants)."""
        try:
            # NSE highlights the ATM row with a specific class
            atm_row = self.driver.find_element(By.CSS_SELECTOR, "tr.atm-straddle, tr.highlight, tr[style*='background']")
            cells = atm_row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 12:
                strike_text = cells[11].text.strip().replace(",", "")
                if "." in strike_text:
                    strike_text = strike_text.split(".")[0]
                if strike_text.isdigit():
                    return int(strike_text)
        except:
            pass
        return None

    def _extract_table_data(self) -> list:
        """Extract option chain data from the table."""
        strikes_data = []
        expiry_date = self._extract_expiry_date()

        try:
            # Find the table body
            table = self.driver.find_element(By.ID, "optionChainTable-indices")
            rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")

            print(f"Found {len(rows)} rows in table")


            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 23:  # NSE table has 23 columns
                        continue

                    # NSE table structure (23 columns):
                    # [0]: Checkbox/empty
                    # [1]: CE OI
                    # [2]: CE OI Change
                    # [3-10]: CE Volume, IV, LTP, Change, Bid Qty, Bid, Ask, Ask Qty
                    # [11]: Strike Price
                    # [12-19]: PE Bid Qty, Bid, Ask, Ask Qty, Change, LTP, IV, Volume
                    # [20]: PE OI Change
                    # [21]: PE OI
                    # [22]: Checkbox/empty

                    # Extract strike price from column 11
                    strike_text = cells[11].text.strip().replace(",", "")
                    if not strike_text:
                        continue

                    # Remove decimal if present (25,650.00 -> 25650)
                    if "." in strike_text:
                        strike_text = strike_text.split(".")[0]

                    if not strike_text.isdigit():
                        continue

                    strike_price = int(strike_text)

                    # CE data: OI (column 1), OI Change (column 2), Volume (column 3), IV (column 4), LTP (column 5)
                    ce_oi = self._parse_number(cells[1].text)
                    ce_oi_change = self._parse_number(cells[2].text)
                    ce_volume = self._parse_number(cells[3].text)
                    ce_iv = self._parse_float(cells[4].text)
                    ce_ltp = self._parse_float(cells[5].text)

                    # PE data: LTP (column 17), IV (column 18), Volume (column 19), OI Change (column 20), OI (column 21)
                    pe_ltp = self._parse_float(cells[17].text)
                    pe_iv = self._parse_float(cells[18].text)
                    pe_volume = self._parse_number(cells[19].text)
                    pe_oi_change = self._parse_number(cells[20].text)
                    pe_oi = self._parse_number(cells[21].text)

                    strikes_data.append({
                        "strikePrice": strike_price,
                        "expiryDate": expiry_date,
                        "CE": {
                            "openInterest": ce_oi,
                            "changeinOpenInterest": ce_oi_change,
                            "volume": ce_volume,
                            "iv": ce_iv,
                            "ltp": ce_ltp
                        },
                        "PE": {
                            "openInterest": pe_oi,
                            "changeinOpenInterest": pe_oi_change,
                            "volume": pe_volume,
                            "iv": pe_iv,
                            "ltp": pe_ltp
                        }
                    })

                except Exception as e:
                    continue

            print(f"Extracted {len(strikes_data)} strikes")

        except Exception as e:
            print(f"Error extracting table data: {e}")
            import traceback
            traceback.print_exc()

        return strikes_data

    def _parse_number(self, text: str) -> int:
        """Parse a number from text, handling commas and dashes."""
        if not text:
            return 0
        text = text.strip().replace(",", "").replace("-", "0")
        try:
            return int(float(text))
        except ValueError:
            return 0

    def _parse_float(self, text: str) -> float:
        """Parse a float from text, handling commas and dashes."""
        if not text:
            return 0.0
        text = text.strip().replace(",", "").replace("-", "0")
        try:
            return float(text)
        except ValueError:
            return 0.0

    def fetch_india_vix(self) -> Optional[float]:
        """
        Fetch India VIX from NSE homepage.

        Returns:
            India VIX value or None if fetch fails
        """
        try:
            self._init_driver()

            print("Loading NSE homepage for VIX...")
            self.driver.get("https://www.nseindia.com")

            # Wait for page to load
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            wait = WebDriverWait(self.driver, 15)

            time.sleep(2)

            # Try to find VIX value - NSE shows it in the indices section
            # Look for element containing "INDIA VIX"
            try:
                vix_elem = self.driver.find_element(
                    By.XPATH,
                    "//a[contains(text(), 'INDIA VIX')]/following-sibling::*[1] | "
                    "//span[contains(text(), 'INDIA VIX')]/following-sibling::*[1]"
                )
                vix_text = vix_elem.text.strip()
                # Extract number
                import re
                numbers = re.findall(r'[\d,]+\.?\d*', vix_text)
                if numbers:
                    vix_value = float(numbers[0].replace(",", ""))
                    print(f"India VIX: {vix_value}")
                    return vix_value
            except Exception:
                pass

            # Alternative: try the indices API endpoint
            try:
                self.driver.get("https://www.nseindia.com/api/allIndices")
                time.sleep(2)
                # Parse JSON from page body
                import json
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                data = json.loads(body_text)

                for index in data.get("data", []):
                    if "INDIA VIX" in index.get("index", "").upper():
                        vix_value = float(index.get("last", 0))
                        print(f"India VIX (from API): {vix_value}")
                        return vix_value
            except Exception as e:
                print(f"Error fetching VIX from API: {e}")

            return None

        except Exception as e:
            print(f"Error fetching India VIX: {e}")
            return None

    def fetch_futures_data(self) -> Optional[dict]:
        """
        Fetch NIFTY futures OI and price data from NSE.

        Returns:
            dict with:
                - future_price: Current futures price
                - future_oi: Current futures OI
                - future_oi_change: Change in futures OI
                - basis: Futures premium/discount vs spot (in points)
                - basis_pct: Basis as percentage
            Or None if fetch fails
        """
        try:
            self._init_driver()

            print("Loading NSE derivatives page for futures...")
            self.driver.get("https://www.nseindia.com/api/liveEquity-derivatives?index=nse50_fut")

            time.sleep(2)

            # Parse JSON from page body
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            data = json.loads(body_text)

            # Find current month NIFTY futures
            for item in data.get("data", []):
                instrument = item.get("instrument", "")
                if "FUTIDX" in instrument and "NIFTY" in item.get("underlying", "").upper():
                    expiry = item.get("expiryDate", "")
                    # Get current/near month futures
                    future_price = float(item.get("lastPrice", 0))
                    future_oi = int(item.get("openInterest", 0))
                    future_oi_change = int(item.get("changeinOpenInterest", 0))
                    underlying_value = float(item.get("underlyingValue", 0))

                    # Calculate basis
                    basis = future_price - underlying_value if underlying_value > 0 else 0
                    basis_pct = (basis / underlying_value * 100) if underlying_value > 0 else 0

                    print(f"Futures: Price={future_price:.2f}, OI={future_oi:,}, "
                          f"OI Change={future_oi_change:+,}, Basis={basis:.2f} ({basis_pct:.3f}%)")

                    return {
                        "future_price": future_price,
                        "future_oi": future_oi,
                        "future_oi_change": future_oi_change,
                        "basis": basis,
                        "basis_pct": basis_pct,
                        "expiry": expiry
                    }

            # Try alternative API endpoint
            self.driver.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050")
            time.sleep(2)

            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            data = json.loads(body_text)

            # This endpoint may have different structure
            print("Futures data not found in primary endpoint, checking alternatives...")
            return None

        except json.JSONDecodeError as e:
            # API may return HTML instead of JSON - try scraping the derivatives page
            print(f"JSON decode error for futures API: {e}")
            return self._fetch_futures_from_page()
        except Exception as e:
            print(f"Error fetching futures data: {e}")
            return None

    def _fetch_futures_from_page(self) -> Optional[dict]:
        """Fallback: scrape futures data from derivatives page."""
        try:
            self.driver.get("https://www.nseindia.com/market-data/live-market-indices")
            time.sleep(3)

            # Look for NIFTY futures row
            import re
            page_source = self.driver.page_source

            # Try to find futures data in the page
            # This is a fallback - structure may vary
            print("Attempting to scrape futures from page (fallback method)")
            return None

        except Exception as e:
            print(f"Error in futures fallback: {e}")
            return None

    def parse_option_data(self, data: dict) -> Optional[dict]:
        """
        Parse the raw data into structured format.

        Returns:
            dict with:
                - spot_price: Current NIFTY spot price
                - expiry_dates: List of available expiries
                - strikes: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change}
        """
        if not data or "records" not in data:
            return None

        records = data["records"]

        spot_price = records.get("underlyingValue")
        if not spot_price:
            return None

        expiry_dates = records.get("expiryDates", [])
        current_expiry = expiry_dates[0] if expiry_dates else None

        strikes = {}
        for item in records.get("data", []):
            strike_price = item.get("strikePrice")
            if not strike_price:
                continue

            ce_data = item.get("CE", {})
            pe_data = item.get("PE", {})

            strikes[strike_price] = {
                "ce_oi": ce_data.get("openInterest", 0),
                "ce_oi_change": ce_data.get("changeinOpenInterest", 0),
                "ce_volume": ce_data.get("volume", 0),
                "ce_iv": ce_data.get("iv", 0.0),
                "ce_ltp": ce_data.get("ltp", 0.0),
                "pe_oi": pe_data.get("openInterest", 0),
                "pe_oi_change": pe_data.get("changeinOpenInterest", 0),
                "pe_volume": pe_data.get("volume", 0),
                "pe_iv": pe_data.get("iv", 0.0),
                "pe_ltp": pe_data.get("ltp", 0.0),
            }

        return {
            "spot_price": spot_price,
            "expiry_dates": expiry_dates,
            "current_expiry": current_expiry,
            "strikes": strikes,
        }


# For testing
if __name__ == "__main__":
    fetcher = NSEFetcher(headless=True)

    try:
        print("Fetching NIFTY option chain data...")
        raw_data = fetcher.fetch_option_chain()

        if raw_data:
            parsed = fetcher.parse_option_data(raw_data)
            if parsed:
                print(f"\nSpot Price: {parsed['spot_price']}")
                print(f"Current Expiry: {parsed['current_expiry']}")
                print(f"Number of strikes: {len(parsed['strikes'])}")

                spot = parsed['spot_price']
                sorted_strikes = sorted(parsed['strikes'].keys())

                if sorted_strikes:
                    atm_idx = min(range(len(sorted_strikes)),
                                 key=lambda i: abs(sorted_strikes[i] - spot))

                    print(f"\nStrikes around ATM ({sorted_strikes[atm_idx]}):")
                    for i in range(max(0, atm_idx-3), min(len(sorted_strikes), atm_idx+4)):
                        strike = sorted_strikes[i]
                        data = parsed['strikes'][strike]
                        print(f"  {strike}: CE OI={data['ce_oi']:,} ({data['ce_oi_change']:+,}) LTP={data['ce_ltp']:.2f} | "
                              f"PE OI={data['pe_oi']:,} ({data['pe_oi_change']:+,}) LTP={data['pe_ltp']:.2f}")
            else:
                print("Failed to parse data")
        else:
            print("Failed to fetch data")
    finally:
        fetcher.close()
