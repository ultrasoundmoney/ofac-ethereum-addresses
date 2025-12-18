import csv
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

def extract_addresses(text):
    """Extract 0x... addresses from any digital currency entry.
    
    Looks for patterns like:
    - "Digital Currency Address -"
    Then keeps only addresses that start with '0x'.
    """
    if not text or text == "-0-":
        return []
    
    addresses = []
    # Match any digital currency address (any currency), capture the address token
    pattern = r'(?:alt\.\s*)?Digital Currency Address\s*-\s*\w+\s+([A-Za-z0-9]+)'
    
    matches = re.finditer(pattern, text)
    for match in matches:
        address = match.group(1)
        # Only keep ETH-style addresses starting with 0x
        if address.startswith("0x"):
            addresses.append(address)
    
    return addresses


def is_truncated(additional_info: str) -> bool:
    """Check if the additional info column appears to be truncated.
    
    CSV truncation detection: Entries are truncated at 1000 characters.
    If an entry is near this limit (>= 990 chars), contains "Digital Currency Address",
    and doesn't end with proper punctuation, it's likely truncated.
    """
    if not additional_info or additional_info == "-0-":
        return False
    
    # Check if it contains digital currency addresses
    if 'Digital Currency Address' not in additional_info:
        return False
    
    # Check if length is near the 1000 character limit (truncation point)
    # Using 990 as threshold to catch entries at the limit
    if len(additional_info) >= 990:
        stripped = additional_info.strip()
        # If it doesn't end with proper punctuation, it's likely truncated
        # (valid entries typically end with '.' or ';')
        if not stripped.endswith('.') and not stripped.endswith(';'):
            return True
    
    return False


def query_ofac_search(name: str) -> list:
    """Query OFAC sanctions search page for a name and return results.
    
    Returns:
        List of dicts with keys: name, address, type, program, list_type, score, detail_url
    """
    search_url = "https://sanctionssearch.ofac.treas.gov/"
    results = []
    
    with requests.Session() as session:
        try:
            print(f"    Querying OFAC for: {name}", file=sys.stderr)
            response = session.get(search_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"    Error fetching search page: {e}", file=sys.stderr)
            return results
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract ViewState and other hidden form fields
        form_data = {}
        
        # Get all hidden inputs
        for hidden_input in soup.find_all("input", type="hidden"):
            input_name = hidden_input.get("name")
            input_value = hidden_input.get("value", "")
            if input_name:
                form_data[input_name] = input_value

        # Set the name field
        form_data["ctl00$MainContent$txtLastName"] = name
        
        # Set other required fields to defaults
        form_data["ctl00$MainContent$ddlType"] = ""
        form_data["ctl00$MainContent$txtID"] = ""
        form_data["ctl00$MainContent$txtAddress"] = ""
        form_data["ctl00$MainContent$txtCity"] = ""
        form_data["ctl00$MainContent$txtState"] = ""
        form_data["ctl00$MainContent$ddlCountry"] = ""
        form_data["ctl00$MainContent$ddlList"] = ""
        form_data["ctl00$MainContent$Slider1"] = "100"
        form_data["ctl00$MainContent$Slider1_Boundcontrol"] = "100"
        form_data["ctl00$MainContent$btnSearch"] = "Search"
        
        try:
            response = session.post(search_url, data=form_data, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"    Error posting search: {e}", file=sys.stderr)
            return results
        
        # Parse results
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Find the results table
        results_table = soup.find("table", id="gvSearchResults")
        
        if not results_table:
            results_div = soup.find("div", id="scrollResults")
            if results_div:
                results_table = results_div.find("table", id="gvSearchResults")
        
        if not results_table:
            all_tables = soup.find_all("table")
            for table in all_tables:
                if table.get("id") == "gvSearchResults":
                    results_table = table
                    break
            
            if not results_table:
                return results

        rows = results_table.find_all("tr")
        
        if not rows:
            return results
        
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 6:
                name_cell = cells[0]
                name_link = name_cell.find("a")
                name_text = name_link.get_text().strip() if name_link else name_cell.get_text().strip()
                detail_url = ""
                if name_link and name_link.get("href"):
                    detail_url = urljoin(search_url, name_link.get("href"))
                
                address = cells[1].get_text().strip()
                entity_type = cells[2].get_text().strip()
                program = cells[3].get_text().strip()
                list_type = cells[4].get_text().strip()
                score = cells[5].get_text().strip()
                
                results.append({
                    "name": name_text,
                    "address": address,
                    "type": entity_type,
                    "program": program,
                    "list_type": list_type,
                    "score": score,
                    "detail_url": detail_url
                })
    
    return results


def get_identification_details(detail_url: str) -> list:
    """Fetch detail page and extract identification information.
    
    Returns:
        List of dicts with keys: type, id_number
    """
    identifications = []
    
    try:
        response = requests.get(detail_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"    Error fetching detail page: {e}", file=sys.stderr)
        return identifications
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Find the identification panel
    ident_panel = soup.find("div", id="ctl00_MainContent_pnlIdentification")
    if not ident_panel:
        return identifications
    
    # Find the identification table
    ident_table = ident_panel.find("table", id="ctl00_MainContent_gvIdentification")
    if not ident_table:
        return identifications
    
    # Extract rows
    tbody = ident_table.find("tbody")
    if tbody:
        all_rows = tbody.find_all("tr")
    else:
        all_rows = ident_table.find_all("tr")
    
    # Skip header row
    rows = all_rows[1:] if len(all_rows) > 1 else []
    
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2:
            id_type = cells[0].get_text().strip()
            id_number = cells[1].get_text().strip()
            
            if not id_type and not id_number:
                continue
            
            identifications.append({
                "type": id_type,
                "id_number": id_number
            })
    
    return identifications


def extract_eth_address_from_identifications(identifications: list) -> list:
    """Extract Ethereum addresses from identifications if present.
    
    Returns:
        List of Ethereum addresses (0x...)
    """
    addresses = []
    for ident in identifications:
        id_type = ident.get("type", "").strip()
        id_number = ident.get("id_number", "").strip()
        
        if id_type.startswith("Digital Currency Address") and id_number.startswith("0x"):
            addresses.append(id_number)
    return addresses


def query_addresses_for_name(name: str) -> list:
    """Query OFAC database for a name and extract all ETH addresses.
    
    Returns:
        List of ETH addresses (0x...)
    """
    addresses = []
    
    search_results = query_ofac_search(name)
    
    for result in search_results:
        # Only process if the found name contains the query name (case-insensitive)
        found_name = result.get('name', '').strip().lower()
        query_name = name.strip().lower()
        if query_name not in found_name:
            continue
        
        if result['detail_url']:
            identifications = get_identification_details(result['detail_url'])
            eth_addresses = extract_eth_address_from_identifications(identifications)
            addresses.extend(eth_addresses)
    
    return addresses


def download_sdn_csv(url: str = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV", save_path: str = "sdn.csv"):
    #Download SDN CSV file from OFAC API.
    try:
        print(f"Downloading SDN CSV from {url}...", file=sys.stderr)
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            f.write(response.content)
        
        print(f"Downloaded {len(response.content)} bytes to {save_path}", file=sys.stderr)
        return save_path
    except requests.RequestException as e:
        print(f"Error downloading SDN CSV: {e}", file=sys.stderr)
        sys.exit(1)


def load_existing_data(csv_path='data.csv'):
    """Load existing data.csv if it exists.
    
    Returns:
        set: Set of (address_lower, name) tuples
    """
    existing = set()
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    addr = row['address'].lower()
                    name = row['name']
                    existing.add((addr, name))
        except Exception:
            pass
    return existing


def generate_stats_table(results):
    """Generate stats table from results.
    
    Returns:
        str: Markdown table with name and address counts
    """
    name_counts = {}
    for address, name in results:
        name_counts[name] = name_counts.get(name, 0) + 1
    
    sorted_names = sorted(name_counts.items())
    total = len(results)
    
    lines = [
        "| sanctioned entity | count |",
        "| :- | -: |"
    ]
    for name, count in sorted_names:
        lines.append(f"| {name} | {count} |")
    lines.append(f"| **total** | **{total}** |")
    
    return "\n".join(lines)


def update_readme(stats_table):
    """Update README.md with stats table."""
    readme_path = 'README.md'
    
    # Read existing README
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        lines = []
    
    # Find stats section
    stats_start = -1
    for i, line in enumerate(lines):
        if line.strip() == "## stats":
            stats_start = i
            break
    
    if stats_start == -1:
        # Add stats section at the end
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append("## stats\n")
        lines.append("\n")
        lines.extend([line + "\n" for line in stats_table.split("\n")])
    else:
        # Find end of stats section (next ## or end of file)
        stats_end = stats_start + 1
        for i in range(stats_start + 1, len(lines)):
            if lines[i].startswith("## ") and i > stats_start + 1:
                stats_end = i
                break
        else:
            stats_end = len(lines)
        
        # Replace stats section
        new_lines = [lines[i] for i in range(stats_start)]
        new_lines.append("## stats\n")
        new_lines.append("\n")
        new_lines.extend([line + "\n" for line in stats_table.split("\n")])
        new_lines.extend(lines[stats_end:])
        lines = new_lines
    
    # Write back
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def process_sdn_csv():
    """Process sdn.csv and extract name-address pairs to data.csv
    
    The script will always download the latest SDN CSV from OFAC API
    and save extracted addresses to data.csv.
    """
    input_file = download_sdn_csv()
    
    # Load existing data to detect updates
    existing_data = load_existing_data()
    
    results = []
    # Global deduplication: track all address-name pairs we've seen
    seen_pairs = set()
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            # The file uses comma delimiter
            reader = csv.reader(f, delimiter=',')
            
            for row_num, row in enumerate(reader, start=1):
                if len(row) < 12:
                    continue  # Skip rows that don't have 12 columns
                
                # Second column (index 1) contains the name
                name = row[1].strip().strip('"')
                
                # Last column (index 11) contains the additional info
                additional_info = row[11].strip().strip('"')
                
                addresses = extract_addresses(additional_info)
                
                # Check if the entry appears truncated
                if is_truncated(additional_info):
                    print(f"Row {row_num}: Detected truncated entry for {name}, querying database...", file=sys.stderr)
                    # Query the database for complete information
                    db_addresses = query_addresses_for_name(name)
                    if db_addresses:
                        print(f"  Found {len(db_addresses)} ETH address(es) from database", file=sys.stderr)
                        # Deduplicate addresses from database (convert to lowercase for comparison)
                        addresses_set = {addr.lower() for addr in addresses}
                        for db_addr in db_addresses:
                            if db_addr.lower() not in addresses_set:
                                addresses.append(db_addr)
                    else:
                        print(f"  No ETH addresses found in database", file=sys.stderr)
                
                # Create a row for each address (global deduplication happens below)
                for address in addresses:
                    # Use lowercase for deduplication comparison
                    pair_key = (address.lower(), name)
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        results.append((address, name))
        
        # Detect updates
        new_entries = []
        for address, name in results:
            pair_key = (address.lower(), name)
            if pair_key not in existing_data:
                new_entries.append((address, name))
        
        # Write results to output CSV
        output_file = 'data.csv'
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            f.write('address,name\n')
            for address, name in results:
                escaped_name = name.replace('"', '""')
                f.write(f'{address},"{escaped_name}"\n')
        
        if new_entries:
            print(f"\nUpdates detected: {len(new_entries)} new address(es)")
            for address, name in new_entries:
                print(f"  {address} - {name}")
        else:
            print("\nNo updates - all addresses already in data.csv")
        
        print(f"\nExtracted {len(results)} address entries from {input_file}")
        print(f"Results written to {output_file}")
        
        # Update README with stats
        stats_table = generate_stats_table(results)
        update_readme(stats_table)
        print("Updated README.md with stats table")
        
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing file: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    process_sdn_csv()