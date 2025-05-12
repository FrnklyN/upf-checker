from flask import Flask, request, jsonify
from flask_cors import CORS
from supermarktconnector.ah import AHConnector
from supermarktconnector.jumbo import JumboConnector
import re

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Initialize connectors
ah_connector = AHConnector()
jumbo_connector = JumboConnector()

# Define E-number pattern for UPF score calculation
e_number_pattern = re.compile(r'E\s*\d{3}[a-z]?', re.IGNORECASE)

def calculate_upf_score(ingredients):
    """
    Calculate UPF score based on ingredients
    Returns a score from 1-10 (1 = minimally processed, 10 = ultra processed)
    """
    if not ingredients or ingredients.lower() in ['geen ingrediënten', 'no ingredients']:
        return 1  # Assume unprocessed if no ingredients
    
    # Count E-numbers (additives)
    e_numbers = len(e_number_pattern.findall(ingredients))
    
    # Count total ingredients
    ingredient_list = [i.strip() for i in ingredients.split(',')]
    total_ingredients = len(ingredient_list)
    
    # Keywords that indicate processing
    processing_keywords = ['gemodificeerd', 'gehydrogeneerd', 'geconcentreerd', 
                          'extract', 'isolaat', 'hydrolysaat', 'maltodextrine', 
                          'glucose', 'fructose', 'siroop', 'verdikkingsmiddel',
                          'emulgator', 'stabilisator', 'conserveermiddel',
                          'smaakversterker', 'kleurstof', 'aroma']
    
    # Count processing keywords
    processing_count = sum(1 for keyword in processing_keywords 
                          if any(keyword in ingredient.lower() for ingredient in ingredient_list))
    
    # Calculate base score
    if total_ingredients <= 5 and e_numbers == 0 and processing_count == 0:
        base_score = 1  # Minimally processed
    elif total_ingredients <= 8 and e_numbers <= 1 and processing_count <= 1:
        base_score = 3  # Processed culinary ingredients
    elif total_ingredients <= 15 and e_numbers <= 3 and processing_count <= 3:
        base_score = 5  # Processed food
    else:
        # Ultra-processed food
        base_score = 7 + min(3, (e_numbers // 2) + (processing_count // 3))
    
    return min(10, base_score)

def process_ah_product(product):
    """Transform AH product data into a standardized format with UPF score"""
    # Extract basic info
    product_id = product.get('webshopId', '')
    name = product.get('title', '')
    brand = product.get('brand', '')
    
    # Price information
    price_obj = product.get('priceBeforeBonus', product.get('currentPrice', {}))
    price = price_obj.get('amount', 0) / 100  # Convert cents to euros
    
    # Extract unit price
    unit_price_obj = product.get('unitPriceDescription', '')
    
    # Get image URL
    images = product.get('images', [])
    image_url = ""
    if images and len(images) > 0:
        image_url = images[0].get('url', '')
    
    # Get description/quantity
    description = product.get('packageSizeText', '')
    
    # Calculate UPF score
    ingredients = product.get('ingredients', '')
    upf_score = calculate_upf_score(ingredients)
    
    return {
        'id': product_id,
        'name': name,
        'brand': brand,
        'description': description,
        'price': price,
        'pricePerUnit': unit_price_obj,
        'store': 'ah',
        'upfScore': upf_score,
        'image': image_url,
        'ingredients': ingredients
    }

def process_jumbo_product(product):
    """Transform Jumbo product data into a standardized format with UPF score"""
    # Extract basic info
    product_id = product.get('id', '')
    name = product.get('title', '')
    
    # Price information
    prices = product.get('prices', {})
    price_obj = prices.get('price', {})
    price = price_obj.get('amount', 0) / 100  # Convert cents to euros
    
    # Unit price
    unit_price = prices.get('unitPrice', {})
    unit = unit_price.get('unit', '')
    unit_price_amount = unit_price.get('price', {}).get('amount', 0) / 100
    unit_price_str = f"€{unit_price_amount:.2f}/{unit}" if unit else ""
    
    # Get image URL
    image_info = product.get('imageInfo', {})
    primary_view = image_info.get('primaryView', [])
    image_url = ""
    if primary_view and len(primary_view) > 0:
        image_url = primary_view[0].get('url', '')
    
    # Get description/quantity
    description = product.get('quantity', '')
    
    # Get additional product data for ingredients
    detailed_product = {}
    try:
        detailed_product = jumbo_connector.get_product(product_id)
    except:
        pass
    
    # Get ingredients and calculate UPF score
    ingredients = ""
    if detailed_product:
        ingredients = detailed_product.get('data', {}).get('description', {}).get('ingredients', '')
    
    upf_score = calculate_upf_score(ingredients)
    
    # Extract brand from title or use empty string
    brand_parts = name.split()
    brand = brand_parts[0] if len(brand_parts) > 0 else "Jumbo"
    
    return {
        'id': product_id,
        'name': name,
        'brand': brand,
        'description': description,
        'price': price,
        'pricePerUnit': unit_price_str,
        'store': 'jumbo',
        'upfScore': upf_score,
        'image': image_url,
        'ingredients': ingredients
    }

@app.route('/api/search', methods=['GET'])
def search_products():
    query = request.args.get('query', '')
    store = request.args.get('store', 'both')
    
    if not query:
        return jsonify({'error': 'Query parameter is required'}), 400
    
    results = []
    
    # Search Albert Heijn products
    if store in ['ah', 'both']:
        try:
            print(f"Searching AH for: {query}")
            ah_products = ah_connector.search_products(query=query, size=25, page=0)
            print(f"AH returned {len(ah_products.get('products', []))} products")
            if 'products' in ah_products and len(ah_products['products']) > 0:
                for product in ah_products['products']:
                    processed_product = process_ah_product(product)
                    results.append(processed_product)
        except Exception as e:
            print(f"Error fetching AH products: {e}")
    
    # Search Jumbo products
    if store in ['jumbo', 'both']:
        try:
            print(f"Searching Jumbo for: {query}")
            jumbo_products = jumbo_connector.search_products(query=query, size=25, page=0)
            data_products = jumbo_products.get('products', {}).get('data', [])
            print(f"Jumbo returned {len(data_products)} products")
            if 'products' in jumbo_products and 'data' in jumbo_products['products']:
                for product in jumbo_products['products']['data']:
                    processed_product = process_jumbo_product(product)
                    results.append(processed_product)
        except Exception as e:
            print(f"Error fetching Jumbo products: {e}")
    
    # Sort by UPF score (lowest first)
    results.sort(key=lambda x: x['upfScore'])
    
    print(f"Total results after processing: {len(results)}")
    
    return jsonify({'products': results})

@app.route('/')
def index():
    return jsonify({
        'name': 'UPF Checker API',
        'version': '1.0.0',
        'endpoints': {
            'search': '/api/search?query=SEARCH_TERM&store=STORE_NAME'
        }
    })

if __name__ == '__main__':
    app.run(debug=True)
