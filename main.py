import requests
from datetime import datetime

def get_bitcoin_l2_tvl():
    """Fetch Bitcoin L2 TVL data from DefiLlama"""
    
    url = "https://api.llama.fi/v2/chains"
    
    response = requests.get(url)
    data = response.json()
    
    # Bitcoin L2s we want to track
    bitcoin_l2s = [
        "Core", "Bitlayer", "Bsquared", "BOB", "Rootstock", 
        "Merlin", "Stacks", "AILayer", "BounceBit", "MAP Protocol",
        "BEVM", "Liquid", "Lightning"
    ]
    
    # Filter for Bitcoin L2s and sort by TVL
    results = []
    for chain in data:
        if chain.get("name") in bitcoin_l2s:
            results.append({
                "name": chain.get("name"),
                "tvl": chain.get("tvl", 0)
            })
    
    # Sort by TVL descending
    results.sort(key=lambda x: x["tvl"], reverse=True)
    
    return results[:10]

def main():
    print(f"Bitcoin L2 TVL Rankings - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    data = get_bitcoin_l2_tvl()
    
    for i, chain in enumerate(data, 1):
        tvl_millions = chain["tvl"] / 1_000_000
        print(f"{i:2}. {chain['name']:15} ${tvl_millions:,.1f}M")

if __name__ == "__main__":
    main()
