from flask import Flask, request, jsonify
from flask_cors import CORS
from supermarktconnector.ah import AHConnector
from supermarktconnector.jumbo import JumboConnector
import re
import difflib  # For fuzzy matching

app = Flask(__name__)
# Configure CORS properly to allow requests from Netlify
CORS(app, resources={r"/*": {"origins": ["https://upfchecker.netlify.app", "http://localhost:3000"]}})

# Initialize connectors with timeouts
try:
    ah_connector = AHConnector(timeout=5)  # 5 second timeout
except Exception as e:
    print(f"Error initializing AH connector: {e}")
    ah_connector = None

try:
    jumbo_connector = JumboConnector(timeout=5)  # 5 second timeout
except Exception as e:
    print(f"Error initializing Jumbo connector: {e}")
    jumbo_connector = None

# Define E-number pattern for UPF score calculation
e_number_pattern = re.compile(r'E\s*\d{3}[a-z]?', re.IGNORECASE)

# Sample product data for fallback
SAMPLE_PRODUCTS = [
    {
        'id': 'ah1',
        'name': 'Biologische Tomaten',
        'brand': 'AH Biologisch',
        'description': '500g',
        'price': 2.79,
        'pricePerUnit': '€13,95/kg',
        'store': 'ah',
        'upfScore': 1,
        'image': 'https://static.ah.nl/dam/product/AHI_43545239383733343337?revLabel=1&rendition=800x800_JPG_Q90'
    },
    {
        'id': 'ah2',
        'name': 'Biologische Avocado',
        'brand': 'AH Biologisch',
        'description': 'Per stuk',
        'price': 1.99,
        'pricePerUnit': '€1,99/stuk',
        'store': 'ah',
        'upfScore': 1,
        'image': 'https://static.ah.nl/dam/product/AHI_43545239383733343938?revLabel=1&rendition=800x800_JPG_Q90'
    },
    {
        'id': 'ah3',
        'name': 'Halfvolle Melk',
        'brand': 'Campina',
        'description': '1L',
        'price': 1.29,
        'pricePerUnit': '€1,29/L',
        'store': 'ah',
        'upfScore': 3,
        'image': 'https://static.ah.nl/dam/product/AHI_43545239383531333537?revLabel=1&rendition=800x800_JPG_Q90'
    },
    {
        'id': 'jumbo1',
        'name': 'Volkoren Brood',
        'brand': 'Jumbo Eigen Merk',
        'description': '800g',
        'price': 1.89,
        'pricePerUnit': '€2,36/kg',
        'store': 'jumbo',
        'upfScore': 4,
        'image': 'https://jumbocoek.centracdn.net/oim/images/s/product/01/31/03/0000000131034/0000000131034_2_800x800.png'
    },
    {
        'id': 'jumbo2',
        'name': 'Yoghurt Naturel',
        'brand': 'Zuivelhoeve',
        'description': '1kg',
        'price': 2.49,
        'pricePerUnit': '€2,49/kg',
        'store': 'jumbo',
        'upfScore': 2,
        'image': 'https://jumbocoek.centracdn.net/oim/images/s/product/00/78/88/0000000078889/0000000078889_2_800x800.png'
    }
]

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
    try:
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
    except Exception as e:
        print(f"Error processing AH product: {e}")
        # Return a minimal product object to avoid breaking the app
        return {
            'id': product.get('webshopId', 'unknown'),
            'name': product.get('title', 'Onbekend product'),
            'brand': product.get('brand', 'Albert Heijn'),
            'description': '',
            'price': 0,
            'pricePerUnit': '',
            'store': 'ah',
            'upfScore': 5,  # Neutrale score
            'image': '',
            'ingredients': ''
        }

def process_jumbo_product(product):
    """Transform Jumbo product data into a standardized format with UPF score"""
    try:
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
        if detailed_product and isinstance(detailed_product, dict):
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
    except Exception as e:
        print(f"Error processing Jumbo product: {e}")
        # Return a minimal product object to avoid breaking the app
        return {
            'id': product.get('id', 'unknown'),
            'name': product.get('title', 'Onbekend product'),
            'brand': "Jumbo",
            'description': '',
            'price': 0,
            'pricePerUnit': '',
            'store': 'jumbo',
            'upfScore': 5,  # Neutrale score
            'image': '',
            'ingredients': ''
        }

def perform_fuzzy_search(products, query, threshold=0.6):
    """
    Apply fuzzy search to filter products that are similar to the query
    Args:
        products: List of products to filter
        query: Search query
        threshold: Similarity threshold (0-1)
    Returns:
        List of products that match the fuzzy search
    """
    query = query.lower()
    query_words = query.split()
    
    # Pre-filter: Keep products that have at least one word in common
    filtered_products = []
    
    for product in products:
        product_name = product.get('name', '').lower()
        product_brand = product.get('brand', '').lower()
        product_text = f"{product_name} {product_brand}"
        
        # Calculate text similarity using difflib
        similarity = difflib.SequenceMatcher(None, query, product_text).ratio()
        
        # Also check if any individual words match
        word_match = any(word in product_text for word in query_words)
        
        # Add product if similarity is above threshold or any words match
        if similarity >= threshold or word_match:
            # Add similarity score for sorting
            product['_similarity'] = similarity
            filtered_products.append(product)
    
    # Sort by similarity (highest first)
    filtered_products.sort(key=lambda x: x.get('_similarity', 0), reverse=True)
    
    # Remove temp similarity score
    for product in filtered_products:
        if '_similarity' in product:
            del product['_similarity']
    
    return filtered_products

@app.route('/api/search', methods=['GET'])
def search_products():
    query = request.args.get('query', '')
    store = request.args.get('store', 'both')
    use_fuzzy = request.args.get('fuzzy', 'false').lower() == 'true'
    
    if not query:
        return jsonify({'error': 'Query parameter is required'}), 400
    
    results = []
    
    # Search Albert Heijn products
    if store in ['ah', 'both']:
        try:
            print(f"Searching AH for: {query}")
            ah_products = ah_connector.search_products(query=query, size=25, page=0)
            
            # Fix: Check if ah_products is a dictionary before using .get()
            if isinstance(ah_products, dict):
                product_count = len(ah_products.get('products', []))
                print(f"AH returned {product_count} products")
                
                if 'products' in ah_products and len(ah_products['products']) > 0:
                    for product in ah_products['products']:
                        processed_product = process_ah_product(product)
                        results.append(processed_product)
            else:
                print(f"Error: Unexpected response from AH API - {type(ah_products)}")
        except Exception as e:
            print(f"Error fetching AH products: {e}")
    
    # Search Jumbo products
    if store in ['jumbo', 'both']:
        try:
            print(f"Searching Jumbo for: {query}")
            jumbo_products = jumbo_connector.search_products(query=query, size=25, page=0)
            
            # Fix: Check if jumbo_products is a dictionary
            if isinstance(jumbo_products, dict) and 'products' in jumbo_products:
                data_products = jumbo_products.get('products', {}).get('data', [])
                print(f"Jumbo returned {len(data_products)} products")
                
                if 'data' in jumbo_products['products']:
                    for product in jumbo_products['products']['data']:
                        processed_product = process_jumbo_product(product)
                        results.append(processed_product)
            else:
                print(f"Error: Unexpected response from Jumbo API - {type(jumbo_products)}")
        except Exception as e:
            print(f"Error fetching Jumbo products: {e}")
    
    # Apply fuzzy search if requested and we have few results
    if use_fuzzy or len(results) < 5:
        # If we have few results, try a broader search
        if len(results) < 5:
            split_words = query.split()
            if len(split_words) > 1:
                # Try searching with just the first word
                print(f"Few results, trying broader search with: {split_words[0]}")
                
                # Albert Heijn broader search
                if store in ['ah', 'both']:
                    try:
                        ah_products = ah_connector.search_products(query=split_words[0], size=25, page=0)
                        # Fix: Add check for dictionary
                        if isinstance(ah_products, dict) and 'products' in ah_products and len(ah_products['products']) > 0:
                            for product in ah_products['products']:
                                processed_product = process_ah_product(product)
                                # Only add if not already in results
                                if not any(r['id'] == processed_product['id'] for r in results):
                                    results.append(processed_product)
                    except Exception as e:
                        print(f"Error in broader AH search: {e}")
                
                # Jumbo broader search
                if store in ['jumbo', 'both']:
                    try:
                        jumbo_products = jumbo_connector.search_products(query=split_words[0], size=25, page=0)
                        # Fix: Add check for dictionary and products key
                        if isinstance(jumbo_products, dict) and 'products' in jumbo_products and 'data' in jumbo_products['products']:
                            for product in jumbo_products['products']['data']:
                                processed_product = process_jumbo_product(product)
                                # Only add if not already in results
                                if not any(r['id'] == processed_product['id'] for r in results):
                                    results.append(processed_product)
                    except Exception as e:
                        print(f"Error in broader Jumbo search: {e}")
        
        # Apply fuzzy matching to all collected results
        if results:  # Only attempt fuzzy search if we have results
            results = perform_fuzzy_search(results, query)
    
    # Sort by UPF score (lowest first)
    results.sort(key=lambda x: x['upfScore'])
    
    print(f"Total results after processing: {len(results)}")
    
    return jsonify({'products': results})

@app.route('/api/sample', methods=['GET'])
def sample_products():
    """Return sample product data for testing or when APIs are unavailable"""
    query = request.args.get('query', '').lower()
    
    if not query:
        return jsonify({'products': SAMPLE_PRODUCTS})
    
    # Simple filtering
    filtered_products = [
        product for product in SAMPLE_PRODUCTS
        if query in product['name'].lower() or query in product['brand'].lower()
    ]
    
    return jsonify({'products': filtered_products})

@app.route('/')
def index():
    return jsonify({
        'name': 'UPF Checker API',
        'version': '1.0.0',
        'endpoints': {
            'search': '/api/search?query=SEARCH_TERM&store=STORE_NAME&fuzzy=true/false'
        }
    })

if __name__ == '__main__':
    app.run(debug=True)
