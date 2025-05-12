from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
import os
from dotenv import load_dotenv

# Try to import supermarktconnector, but continue if not available
try:
    from supermarktconnector.jumbo import JumboConnector
    from supermarktconnector.ah import AHConnector
    CONNECTORS_AVAILABLE = True
except ImportError:
    print("Warning: supermarktconnector not available, using mock data")
    CONNECTORS_AVAILABLE = False

# Load environment variables
load_dotenv()

app = FastAPI(title="UPF Checker API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define models
class UnitPrice(BaseModel):
    unit: str
    price: float

class Product(BaseModel):
    id: str
    title: str
    brand: Optional[str] = None
    image: Optional[str] = None
    price: float
    unitPrice: Optional[UnitPrice] = None
    upfScore: int
    supermarket: str
    ingredients: Optional[str] = None
    quantity: Optional[str] = None
    categoryId: Optional[str] = None
    categoryName: Optional[str] = None
    url: Optional[str] = None

# Mock data for when connectors are not available
MOCK_PRODUCTS = [
    {
        "id": "ah-1",
        "title": "Biologische Tomaten",
        "brand": "AH Biologisch",
        "image": "https://static.ah.nl/dam/product/AHI_434d50303235353737_1_LowRes_JPG.jpg",
        "price": 2.79,
        "unitPrice": {
            "unit": "kg",
            "price": 13.95
        },
        "upfScore": 1,
        "supermarket": "ah",
        "ingredients": "Tomaat",
        "quantity": "500g"
    },
    # More mock products...
]

# Simple UPF score calculation
def calculate_upf_score(ingredients: str) -> int:
    """Simple UPF score calculation based on ingredients"""
    if not ingredients:
        return 5
    
    # Additives (E-numbers) increase score
    additives = ['e-', 'e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e9']
    additive_count = sum(1 for a in additives if a in ingredients.lower())
    
    # Processed ingredients increase score
    processed = ['suiker', 'zout', 'vet', 'olie', 'zetmeel', 'siroop', 'bloem', 
                 'gemodificeerd', 'gehydrogeneerd', 'extract', 'concentraat']
    processed_count = sum(1 for p in processed if p in ingredients.lower())
    
    # Whole foods decrease score
    whole_foods = ['groente', 'fruit', 'noten', 'zaden', 'granen', 'peulvruchten',
                  'vlees', 'vis', 'ei', 'melk']
    whole_foods_count = sum(1 for f in whole_foods if f in ingredients.lower())
    
    # Calculate score
    score = 5  # Base score
    score += min(additive_count, 3)
    score += min(processed_count // 2, 2)
    score -= min(whole_foods_count, 3)
    
    return max(1, min(10, score))

# Initialize connectors if available
ah_connector = None
jumbo_connector = None

if CONNECTORS_AVAILABLE:
    try:
        ah_connector = AHConnector()
        jumbo_connector = JumboConnector()
    except Exception as e:
        print(f"Error initializing connectors: {e}")

@app.get("/")
async def root():
    return {"message": "UPF Checker API is running"}

@app.get("/api/search", response_model=Dict[str, List[Product]])
async def search_products(
    query: str = Query(..., description="Search query"),
    supermarket: str = Query("both", description="Supermarket filter (ah, jumbo, or both)")
):
    if not query:
        return {"products": []}
    
    products = []
    
    if not CONNECTORS_AVAILABLE:
        # Use mock data
        filtered_products = [p for p in MOCK_PRODUCTS 
                           if query.lower() in p["title"].lower() and 
                           (supermarket == "both" or p["supermarket"] == supermarket)]
        return {"products": filtered_products}
    
    # Search in Albert Heijn
    if supermarket in ["ah", "both"] and ah_connector:
        try:
            ah_results = ah_connector.search_products(query)
            if ah_results and 'products' in ah_results:
                for product in ah_results['products']:
                    # Process AH product data
                    product_details = None
                    try:
                        # Try to get additional details if available
                        product_details = ah_connector.get_product(product['id'])
                    except:
                        pass
                    
                    ingredients = ""
                    if product_details and 'details' in product_details:
                        for detail in product_details['details'].get('details', []):
                            if detail.get('name') == 'Ingrediënten':
                                ingredients = detail.get('value', '')
                    
                    # Calculate UPF score
                    upf_score = calculate_upf_score(ingredients)
                    
                    # Create product object
                    products.append(Product(
                        id=f"ah-{product['id']}",
                        title=product.get('title', ''),
                        brand=product.get('brand', {}).get('name', None),
                        image=product.get('images', [{}])[0].get('url', None) if product.get('images') else None,
                        price=float(product.get('price', {}).get('amount', 0)) / 100,
                        unitPrice=UnitPrice(
                            unit=product.get('price', {}).get('unitSize', 'stuk'),
                            price=float(product.get('price', {}).get('unitPrice', 0)) / 100
                        ) if product.get('price', {}).get('unitPrice') else None,
                        upfScore=upf_score,
                        supermarket="ah",
                        ingredients=ingredients,
                        quantity=product.get('packageSummary', None),
                        categoryId=str(product.get('categoryId', '')),
                        url=f"https://www.ah.nl/producten/product/{product.get('id', '')}"
                    ))
        except Exception as e:
            print(f"Error searching AH: {e}")
    
    # Search in Jumbo
    if supermarket in ["jumbo", "both"] and jumbo_connector:
        try:
            jumbo_results = jumbo_connector.search_products(query)
            if jumbo_results and 'products' in jumbo_results and 'data' in jumbo_results['products']:
                for product in jumbo_results['products']['data']:
                    # Process Jumbo product data
                    product_details = None
                    try:
                        # Try to get additional details if available
                        product_details = jumbo_connector.get_product(product['id'])
                    except:
                        pass
                    
                    ingredients = ""
                    if product_details and 'data' in product_details:
                        for attr in product_details['data'].get('attributes', []):
                            if attr.get('code') == 'ingredients':
                                ingredients = attr.get('value', '')
                    
                    # Calculate UPF score
                    upf_score = calculate_upf_score(ingredients)
                    
                    # Extract price
                    price = 0
                    if 'prices' in product and 'price' in product['prices']:
                        price = float(product['prices']['price']['amount']) / 100
                    
                    # Extract unit price
                    unit_price = None
                    if 'prices' in product and 'unitPrice' in product['prices']:
                        unit_price = UnitPrice(
                            unit=product['prices']['unitPrice'].get('unit', 'stuk'),
                            price=float(product['prices']['unitPrice']['price']['amount']) / 100
                        )
                    
                    # Create product object
                    products.append(Product(
                        id=f"jumbo-{product['id']}",
                        title=product.get('title', ''),
                        brand=None,  # Jumbo API doesn't seem to provide brand info in search
                        image=product.get('imageInfo', {}).get('primaryView', [{}])[0].get('url', None) 
                              if product.get('imageInfo') and product.get('imageInfo').get('primaryView') else None,
                        price=price,
                        unitPrice=unit_price,
                        upfScore=upf_score,
                        supermarket="jumbo",
                        ingredients=ingredients,
                        quantity=product.get('quantity', None),
                        url=f"https://www.jumbo.com/producten/{product.get('id', '')}"
                    ))
        except Exception as e:
            print(f"Error searching Jumbo: {e}")
    
    # Sort products by UPF score (lowest first)
    products.sort(key=lambda x: x.upfScore)
    
    return {"products": products}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 