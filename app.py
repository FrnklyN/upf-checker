import asyncio
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supermarktconnector.ah import AHConnector
from supermarktconnector.jumbo import JumboConnector
import re
import difflib  # For fuzzy matching
import logging

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- CORS Configuration ---
# Allow all origins for simplicity during development/initial deployment
# For production, restrict this to your Netlify frontend URL
origins = [
    "*" # Allows all origins
    # Example for specific origins:
    # "http://localhost",
    # "http://localhost:xxxx", # If your frontend runs on a specific port locally
    # "https://upfchecker.netlify.app" 
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods (GET, POST, etc.)
    allow_headers=["*"], # Allows all headers
)

# --- Initialize connectors ---
# Note: Initializing these here might be slow if they do I/O on startup.
# Consider lazy initialization within the endpoint if startup time becomes an issue.
try:
    ah_connector = AHConnector()
except Exception as e:
    logger.error(f"Failed to initialize AHConnector: {e}")
    ah_connector = None # Handle potential init failure

try:
    jumbo_connector = JumboConnector()
except Exception as e:
    logger.error(f"Failed to initialize JumboConnector: {e}")
    jumbo_connector = None # Handle potential init failure


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
    ingredient_list = [i.strip().lower() for i in ingredients.split(',') if i.strip()]
    total_ingredients = len(ingredient_list)
    
    # Keywords that indicate processing
    processing_keywords = ['gemodificeerd', 'gehydrogeneerd', 'geconcentreerd', 
                          'extract', 'isolaat', 'hydrolysaat', 'maltodextrine', 
                          'glucose', 'fructose', 'siroop', 'verdikkingsmiddel',
                          'emulgator', 'stabilisator', 'conserveermiddel',
                          'smaakversterker', 'kleurstof', 'aroma']
    
    # Count processing keywords
    processing_count = 0
    if total_ingredients > 0:
        processing_count = sum(1 for keyword in processing_keywords 
                              if any(keyword in ingredient for ingredient in ingredient_list))
    
    # Calculate base score
    score = 1 # Default to minimal
    if total_ingredients > 5 or e_numbers > 0 or processing_count > 0:
        score = 3 # Processed culinary ingredients / Processed food base
        score += (e_numbers * 1.5) # Higher weight for E-numbers
        score += processing_count # Add processing keyword count
        score += (total_ingredients // 5) # Add points for very long ingredient lists

    # Ensure score is between 1 and 10
    final_score = max(1, min(10, round(score)))

    # Debug log for score calculation
    # logger.debug(f"UPF Score Input: Total={total_ingredients}, E={e_numbers}, Proc={processing_count} -> Score: {final_score}")

    return final_score

def process_ah_product(product):
    """Transform AH product data into a standardized format with UPF score"""
    if not isinstance(product, dict):
        logger.warning(f"Received non-dict AH product: {product}")
        return None # Skip this product if data is malformed

    try:
        product_id = product.get('webshopId', '')
        name = product.get('title', 'Unknown Product')
        brand = product.get('brand', 'Unknown Brand')
        
        # --- Robust Price Handling --- 
        price = 0.0
        # Prefer priceBeforeBonus if it exists and is a dict
        price_info = product.get('priceBeforeBonus')
        if not isinstance(price_info, dict):
            # Fallback to currentPrice if priceBeforeBonus is not a dict or doesn't exist
            price_info = product.get('currentPrice')

        # Extract price if we found a valid price dictionary
        if isinstance(price_info, dict):
            price = price_info.get('amount', 0) / 100.0
        # Handle cases where price might be directly a number (assume cents)
        elif isinstance(price_info, (int, float)):
            price = price_info / 100.0 
        # --- End Robust Price Handling ---
        
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
        ingredients = product.get('ingredientsDescription', '') # Use 'ingredientsDescription' for AH
        if not ingredients: # Fallback if 'ingredientsDescription' is empty
            ingredients = product.get('ingredients', '')

        upf_score = calculate_upf_score(ingredients if ingredients else "")
        
        return {
            'id': f"ah_{product_id}", # Add prefix for uniqueness across stores
            'name': name,
            'brand': brand,
            'description': description,
            'price': price,
            'pricePerUnit': unit_price_obj,
            'store': 'ah',
            'upfScore': upf_score,
            'image': image_url,
            'ingredients': ingredients if ingredients else "Ingrediënten niet beschikbaar"
        }
    except Exception as e:
        logger.error(f"Error processing AH product (ID: {product.get('webshopId', 'N/A')}): {e}", exc_info=True)
        return None # Skip product on error

def process_jumbo_product(product, connector):
    """
    Transform Jumbo product data into a standardized format with UPF score.
    Requires the connector instance to fetch detailed product info for ingredients.
    """
    if not isinstance(product, dict):
        logger.warning(f"Received non-dict Jumbo product: {product}")
        return None # Skip if malformed

    if not connector:
        logger.warning("Jumbo connector not available, cannot process Jumbo product fully.")
        return None

    try:
        product_id = product.get('id', '')
        name = product.get('title', 'Unknown Product')
        
        # Price information
        prices = product.get('prices', {})
        price_obj = prices.get('price', {})
        price = price_obj.get('amount', 0) / 100.0 # Convert cents to euros
        
        # Unit price
        unit_price = prices.get('unitPrice', {})
        unit = unit_price.get('unit', '')
        unit_price_amount = unit_price.get('price', {}).get('amount', 0) / 100.0
        unit_price_str = f"€{unit_price_amount:.2f}/{unit}" if unit and unit_price_amount > 0 else ""
        
        # Get image URL
        image_info = product.get('imageInfo', {})
        primary_view = image_info.get('primaryView', [])
        image_url = ""
        if primary_view and len(primary_view) > 0:
            image_url = primary_view[0].get('url', '')
        
        # Get description/quantity
        description = product.get('quantity', '')
        
        # --- Get ingredients using get_product (BLOCKING CALL) ---
        # This call needs to run in FastAPI's thread pool
        ingredients = "Ingrediënten niet beschikbaar"
        try:
            # This is a synchronous/blocking call
            detailed_product = connector.get_product(product_id=product_id)
            if detailed_product:
                # Ingredients path might vary, adjust as needed based on actual API response
                ingredients_data = detailed_product.get('data', {}).get('product', {}).get('sections', [])
                for section in ingredients_data:
                    if section.get('title', '').lower() == 'ingrediënten':
                        # Assuming ingredients are HTML formatted
                        ingredients_html = section.get('content', [{}])[0].get('html', '')
                        # Basic cleanup - needs improvement for proper parsing
                        ingredients = re.sub('<[^<]+?>', '', ingredients_html).strip()
                        break # Stop after finding the ingredients section
                if not ingredients or ingredients.lower() == 'ingrediënten': # Check if only title was found
                    ingredients = detailed_product.get('data', {}).get('product', {}).get('description', {}).get('ingredients', '')
                    if not ingredients:
                        # Fallback - check other potential fields if necessary
                        pass

        except Exception as e:
            logger.warning(f"Could not fetch detailed Jumbo product (ID: {product_id}) for ingredients: {e}")
            # Continue without ingredients if detailed fetch fails

        # Calculate UPF score
        upf_score = calculate_upf_score(ingredients if ingredients else "")
        
        # Extract brand from title (simple split) or default
        brand_parts = name.split()
        brand = brand_parts[0] if len(brand_parts) > 0 else "Jumbo"
        
        return {
            'id': f"jumbo_{product_id}", # Add prefix for uniqueness
            'name': name,
            'brand': brand,
            'description': description,
            'price': price,
            'pricePerUnit': unit_price_str,
            'store': 'jumbo',
            'upfScore': upf_score,
            'image': image_url,
            'ingredients': ingredients if ingredients else "Ingrediënten niet beschikbaar"
        }
    except Exception as e:
        logger.error(f"Error processing Jumbo product (ID: {product.get('id', 'N/A')}): {e}", exc_info=True)
        return None # Skip product on error

def perform_fuzzy_search(products, query, threshold=0.4): # Lowered threshold for more potential matches
    """
    Apply fuzzy search to filter products that are similar to the query.
    Improved logic for matching.
    """
    if not query:
        return products # Return all if query is empty

    query = query.lower()
    query_words = set(query.split())

    matched_products = []

    for product in products:
        if not product: continue # Skip None products from processing errors

        product_name = product.get('name', '').lower()
        product_brand = product.get('brand', '').lower()
        search_text = f"{product_name} {product_brand}"
        product_words = set(search_text.split())

        # Calculate similarity using SequenceMatcher (good for typos/order)
        similarity = difflib.SequenceMatcher(None, query, search_text).ratio()

        # Calculate Jaccard similarity for word overlap (good for matching keywords)
        intersection = len(query_words.intersection(product_words))
        union = len(query_words.union(product_words))
        jaccard_similarity = intersection / union if union > 0 else 0

        # Combine scores (adjust weighting as needed)
        # Give higher weight to SequenceMatcher for overall resemblance,
        # but ensure some word overlap contributes significantly.
        combined_score = (similarity * 0.7) + (jaccard_similarity * 0.3)

        # logger.debug(f"Fuzzy Check: Query='{query}', Text='{search_text}', SeqMatch={similarity:.2f}, Jaccard={jaccard_similarity:.2f}, Combined={combined_score:.2f}")

        # Keep product if combined score meets threshold
        if combined_score >= threshold:
            product['_similarity'] = combined_score # Store for sorting
            matched_products.append(product)

    # Sort by similarity (highest first)
    matched_products.sort(key=lambda x: x.get('_similarity', 0), reverse=True)

    # Remove temporary similarity score before returning
    for product in matched_products:
        if '_similarity' in product:
            del product['_similarity']

    logger.info(f"Fuzzy search for '{query}': {len(products)} -> {len(matched_products)} results (Threshold: {threshold})")
    return matched_products

# --- API Endpoints ---

@app.get("/")
async def index():
    """ Basic status endpoint """
    logger.info("Root endpoint '/' accessed")
    return {"status": "UPF Checker API is running!",
            "ah_connector_status": "Initialized" if ah_connector else "Failed",
            "jumbo_connector_status": "Initialized" if jumbo_connector else "Failed"}

@app.get("/api/search")
async def search_products(
    query: str = Query(..., min_length=1, description="Search term for products"),
    store: str = Query("both", description="Store to search ('ah', 'jumbo', or 'both')"),
    fuzzy: bool = Query(False, description="Enable fuzzy search matching"),
    limit: int = Query(50, ge=1, le=100, description="Total number of results desired") # Limit total results
):
    """
    Search for products in Albert Heijn and/or Jumbo based on a query.
    Returns a list of products sorted by UPF score (ascending).
    """
    logger.info(f"Search request received: query='{query}', store='{store}', fuzzy={fuzzy}, limit={limit}")

    if store not in ['ah', 'jumbo', 'both']:
        raise HTTPException(status_code=400, detail="Invalid store parameter. Use 'ah', 'jumbo', or 'both'.")

    # Determine how many items to request per store based on the total limit
    # Request slightly more to account for processing errors or non-matches before fuzzy search
    fetch_size_per_store = (limit // (1 if store != 'both' else 2)) + 10 # Add buffer
    fetch_size_per_store = min(fetch_size_per_store, 50) # Cap per store fetch size (API limits?)

    results = []
    tasks = []

    # --- Define Async Tasks for Store Searches ---
    async def run_ah_search(q, size):
        if not ah_connector:
            logger.warning("AH Connector not available, skipping AH search.")
            return []
        try:
            logger.info(f"Starting AH search for '{q}' with size {size}")
            # Run synchronous blocking call in thread pool
            ah_raw_products = await asyncio.to_thread(ah_connector.search_products, query=q, size=size, page=0)
            ah_processed = []
            products_list = ah_raw_products.get('products', [])
            logger.info(f"AH search returned {len(products_list)} raw products.")
            if products_list:
                for product in products_list:
                    processed = process_ah_product(product)
                    if processed: # Add only if processing succeeded
                        ah_processed.append(processed)
            logger.info(f"AH search yielded {len(ah_processed)} processed products.")
            return ah_processed
        except Exception as e:
            logger.error(f"Error during AH search task for '{q}': {e}", exc_info=True)
            return [] # Return empty list on error

    async def run_jumbo_search(q, size):
        if not jumbo_connector:
            logger.warning("Jumbo Connector not available, skipping Jumbo search.")
            return []
        try:
            logger.info(f"Starting Jumbo search for '{q}' with size {size}")
            # Run synchronous blocking call in thread pool
            jumbo_raw_products = await asyncio.to_thread(jumbo_connector.search_products, query=q, size=size, page=0)
            jumbo_processed = []
            products_list = jumbo_raw_products.get('products', {}).get('data', [])
            logger.info(f"Jumbo search returned {len(products_list)} raw products.")
            if products_list:
                for product in products_list:
                    # Pass connector for detail fetch
                    processed = await asyncio.to_thread(process_jumbo_product, product, jumbo_connector)
                    if processed: # Add only if processing succeeded
                        jumbo_processed.append(processed)
            logger.info(f"Jumbo search yielded {len(jumbo_processed)} processed products.")
            return jumbo_processed
        except Exception as e:
            logger.error(f"Error during Jumbo search task for '{q}': {e}", exc_info=True)
            return [] # Return empty list on error

    # --- Schedule Tasks ---
    if store == 'ah' or store == 'both':
        tasks.append(run_ah_search(query, fetch_size_per_store))

    if store == 'jumbo' or store == 'both':
        tasks.append(run_jumbo_search(query, fetch_size_per_store))

    # --- Execute Tasks Concurrently ---
    if tasks:
        logger.info(f"Running {len(tasks)} search tasks concurrently...")
        task_results = await asyncio.gather(*tasks)
        for result_list in task_results:
            results.extend(result_list) # Combine results from all tasks
        logger.info(f"Combined results from stores: {len(results)} products before filtering/sorting.")
    else:
        logger.warning("No search tasks were scheduled.")
        results = []

    # --- Apply Fuzzy Search if requested ---
    if fuzzy and query:
        logger.info(f"Applying fuzzy search with query: '{query}'")
        # Run potentially CPU-bound fuzzy search in thread pool
        results = await asyncio.to_thread(perform_fuzzy_search, results, query)
        logger.info(f"Fuzzy search resulted in {len(results)} products.")

    # --- Sort final results by UPF Score (ascending) ---
    # Handle potential None values in price during sort
    results.sort(key=lambda x: (x.get('upfScore', 11), x.get('price', float('inf')) if x else float('inf'))) # Sort by UPF score (lowest first), then price (lowest first)

    # --- Limit Results ---
    final_results = results[:limit]
    logger.info(f"Final sorted and limited results count: {len(final_results)}")

    return {"products": final_results}

# --- Add Uvicorn entry point for local development (optional) ---
# This allows running `python app.py` locally
# Render uses the Procfile/command directly (`uvicorn app:app --host 0.0.0.0 ...`)
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server locally on http://127.0.0.1:8000")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
