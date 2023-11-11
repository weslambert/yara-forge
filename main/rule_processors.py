import sys
import dateparser
import logging
from pprint import pprint
from git import Repo

# Date Lookup Cache
date_lookup_cache = {}

# Private YARA rules
private_rule_mapping = []

# Process the YARA rules
def process_yara_rules(yara_rule_repo_sets):
   # Loop over the repositories
   for repo in yara_rule_repo_sets:
      
      # Rule set identifier
      rule_set_id = repo['name'].replace(" ", "_").upper()
      
      # Debug output
      logging.log(logging.INFO, "Processing YARA rules from repository: %s" % repo['name'])
      
      # Loop over the rule sets in the repository and modify the rules
      num_rules = 0
      for rules in repo['rules_sets']:
         # Debug output
         logging.log(logging.DEBUG, "Processing YARA rules from rule set: %s" % rules['file_path'])
         # Loop over each of the rules and modify them
         for rule in rules['rules']:
            # Debug output
            logging.log(logging.DEBUG, "Processing YARA rule: %s" % rule['rule_name'])

            # Rule Meta Data Modifications ----------------------------------------------

            # Check if the rule is a private rule
            is_private_rule = False
            if 'scopes' in rule:
               if 'private' in rule['scopes']:
                  is_private_rule = True

            # Add metadata to rules that don't have any
            if 'metadata' not in rule:
               rule['metadata'] = []

            # Adding additional meta data values ----------------------------------------
            # Add a quality value based on the original repo
            modify_meta_data_value(rule['metadata'], 'quality', repo['quality'])
            # Add a rule source URL to the original file
            modify_meta_data_value(rule['metadata'], 'source_url', f'{repo["url"]}/blob/{repo["branch"]}/{rules["file_path"]}')

            # Modifying existing meta data values ---------------------------------------

            # Modify the rule references
            rule['metadata'] = align_yara_rule_reference(rule['metadata'], repo['url'])
            
            # Modify the rule date
            rule['metadata'] = align_yara_rule_date(rule['metadata'], repo['repo_path'], rules['file_path'])
            
            # Modify the rule hashes
            rule['metadata'] = align_yara_rule_hashes(rule['metadata'])
            
            # # Modify the rule tags
            # rule['metadata'] = process_yara_rule_tags(rule['metadata'], repo['tags'])
            
            # # Modify the rule description
            rule['metadata'] = align_yara_rule_description(rule['metadata'], repo['name'])
            
            # Modify the rule author
            rule['metadata'] = align_yara_rule_author(rule['metadata'], repo['author'])
            
            # Add a score based on the rule quality and meta data keywords 
            rule_score = evaluate_yara_rule_score(rule)
            modify_meta_data_value(rule['metadata'], 'score', rule_score)

            # Modify the rule name
            rule_name_old = rule['rule_name']
            rule_name_new = align_yara_rule_name(rule['rule_name'], rule_set_id)
            # If the rule is private, add the _PRIVATE suffix and 
            if is_private_rule:
               rule_name_new = f"{rule_name_new}_PRIVATE"
               # Add the rule to the private rule mapping
               private_rule_mapping.append({
                  "old_name": rule_name_old,
                  "new_name": rule_name_new,
                  "rule": rule
               })
            # Set the new rule name
            rule['rule_name'] = rule_name_new
            
            # Check if the rule uses private rules
            private_rules_used = check_rule_uses_private_rules(rule, private_rule_mapping)
            if private_rules_used:
               # Change the condition terms of the rule to align them with the new private rule names
               rule['condition_terms'] = adjust_identifier_names(rule['condition_terms'], private_rules_used)
            # Add the private rules used to the rule
            rule['private_rules_used'] = private_rules_used
            logging.debug(f"Private rules used: {private_rules_used}")

            # Count the number of rules
            num_rules += 1

      # Info output about the number of rules in the repository
      logging.log(logging.INFO, f"Normalized {num_rules} rules from repository: {repo['name']}")

   return yara_rule_repo_sets

# Adjust the identifier names of a rule to align them with the new private rule names
def adjust_identifier_names(condition_terms, private_rules_used):
   # Loop over the private rules used
   for private_rule in private_rules_used:
      # Loop over the condition terms
      for i, condition_term in enumerate(condition_terms):
         # Check if the condition term is the private rule
         if condition_term == private_rule['old_name']:
            # Replace the condition term with the new private rule name
            condition_terms[i] = private_rule['new_name']
   return condition_terms

# Check if the rule uses private rules
def check_rule_uses_private_rules(rule, private_rule_mapping):
   # List of private rules used
   private_rules_used = []
   # Loop over the private rules
   for private_rule in private_rule_mapping:
      # Check if the rule uses the private rule
      if private_rule['old_name'] in rule['condition_terms']:
         # Add the private rule to the list of private rules used
         private_rules_used.append(private_rule)
   return private_rules_used

# Check if there's a description set in the YARA rule and if not, add the repository description
def align_yara_rule_description(rule_meta_data, repo_description):
   # List of possible description names
   description_names = ['description', 'desc', 'details', 'information', 'info', 'notes', 'abstract', 'explanation', 'rationale']
   description_values_prefixes = ['Detects ']
   threat_names = ['threat_name', 'threat', 'malware', 'mal', 'malware_name', 'mal_name', 'threat_type', 'threat_category', 'threat_family', 'threat_group',]
   # Look for the description in the rule meta data
   description_found = False
   description_value = f"No description has been set in the source file - {repo_description}"
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for meta_data in meta_data_copy:
      for key, value in meta_data.items():
         # If the key is in the list of possible description names, then we found the description
         if key in description_names:
            description_found = True
            description_value = value
            # Remove the description from the original meta data
            rule_meta_data.remove(meta_data)
         # If the value starts with one of the prefixes, then we found the description
         elif isinstance(value, str) and value.startswith(tuple(description_values_prefixes)):
            description_found = True
            description_value = value
            # Remove the description from the original meta data
            rule_meta_data.remove(meta_data)
      # If we couldn't find a description so far, we use the first threat name we can find
      if not description_found:
         for key, value in meta_data.items():
            # If we can find a threat name, we use it to formulate a description
            if key.lower() in threat_names:
               description_found = True
               # If the threat name contains a period or dash we replace it and put the original name in brackets
               description_value = f"Detects {value.replace('.', ' ').replace('-', ' ').title()} ({value})"
               # Remove the description from the original meta data
               rule_meta_data.remove(meta_data)
   # Lower the quality score if the descriptions hasn't been set
   if not description_found:
      modify_yara_rule_quality(rule_meta_data, -5)
   # Set the new description
   rule_meta_data.append({'description': description_value})
   return rule_meta_data


# Check for all the hash values in the meta data and align them to the key value 'hash'
def align_yara_rule_hashes(rule_meta_data):
   # List of possible hash names
   hash_names = ['hash', 'hashes', 'md5', 'sha1', 'sha256', 'sha512', 'sha-1', 'sha-256', 'sha-512', 'sha_256', 'sha_1', 'sha_512', 'md5sum', 'sha1sum', 'sha256sum', 'sha512sum', 'md5sums', 'sha1sums', 'sha256sums', 'sha512sums', 'reference_sample', 'sample']
   # Look for the hashes in the rule meta data
   hashes_found = False
   hashes_values = []
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for mdata in meta_data_copy:
      for key, value in mdata.items():
         # If the key is in the list of possible hash names, then we found the hashes
         if key.lower() in hash_names:
            hashes_found = True
            hashes_values.append(value.lower())
            # Remove the hashes from the original meta data
            rule_meta_data.remove(mdata)
   # If the hashes are found, modify them
   if hashes_found:
      for value in hashes_values:
         rule_meta_data.append({'hash': value})
   return rule_meta_data


# Modify the quality score of a YARA rule
def modify_yara_rule_quality(rule_meta_data, reduction_value):
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for mdata in meta_data_copy:
      for k, v in mdata.items():
         # If the key is in the meta data, then we modify it
         if k == "quality":
            mdata[k] += reduction_value
            return meta_data_copy
   return rule_meta_data

# Modify a value in the meta data, if it exists, otherwise add it
def modify_meta_data_value(rule_meta_data, key, value):
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for mdata in meta_data_copy:
      for k, v in mdata.items():
         # If the key is in the meta data, then we modify it
         if k == key:
            mdata[k] = value
            return mdata
   # If the key is not in the meta data, then we add it
   rule_meta_data.append({key: value})
   return rule_meta_data


# Evaluate the YARA rule score
def evaluate_yara_rule_score(rule):
   # Score for the rule quality
   base_rule_score = 75
   # Score for the rule quality
   #quality_modifier = evaluate_yara_rule_quality(rule)
   # Score for the rule meta data
   meta_data_modifier = evaluate_yara_rule_meta_data(rule)
   # Score for the rule strings
   rule_score = base_rule_score + meta_data_modifier
   return rule_score

# Evaluate the score modifier based on the rule meta data
def evaluate_yara_rule_meta_data(rule):
   # List of possible meta data keywords
   meta_data_keywords_suspicious = ['hunting', 'experimental', 'test', 'testing', 'false positive', 'unstable', 'untested', 'unverified', 'unreliable', 'unconfirmed']
   # Check if one of the keywords appears in the meta data values
   for meta_data in rule['metadata']:
      for key, value in meta_data.items():
         if isinstance(value, str) and value.lower() in meta_data_keywords_suspicious:
            return -15
   # Check if one of the keywords appears in the rule name
   for keyword in meta_data_keywords_suspicious:
      if keyword in rule['rule_name'].lower():
         return -15
   return 0

# Change YARA rule author
def align_yara_rule_author(rule_meta_data, repo_author):
   # List of possible author names
   author_names = ['author', 'authors', 'writer', 'creator', 'created_by', 'created_by', 'copyright', 'made_by', 'contributor', 'contributed_by']
   # Look for the author in the rule meta data
   author_found = False
   author_value = ""
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for meta_data in meta_data_copy:
      for key, value in meta_data.items():
         # If the key is in the list of possible author names, then we found the author
         if key in author_names:
            author_found = True
            author_value = value
            # Remove the author from the original meta data
            rule_meta_data.remove(meta_data)
   # If the author is found, modify it
   if author_found:
      rule_meta_data.append({'author': author_value})
   # If the author is not found, add it
   if not author_found:
      rule_meta_data.append({'author': repo_author})
   return rule_meta_data


# Change YARA rule name
def align_yara_rule_name(rule_name, rule_set_id):
   # New name elements
   new_name_elements = []
   # Add the rule set identifier 
   new_name_elements.append(rule_set_id)
   # Dissect the rule name
   name_elements = rule_name.split("_")
   # Change every element of the rule
   for element in name_elements:
      # If the element is already all uppercase, add it to the new name
      if element.isupper():
         new_name_elements.append(element)
         continue
      # If the element is all lowercase or anything else, then title case it
      else:
         new_name_elements.append(element.title())
   return "_".join(new_name_elements)


# Modify the YARA rule references
def align_yara_rule_reference(rule_meta_data, rule_set_url):
   # List of possible reference names
   other_ref_names = ['reference', 'references', 'ref', 'url', 'source', 'link', 'website', 'webpage']
   other_indicators = ['http://', 'https://']
   # Look for the reference in the rule meta data
   reference_found = False
   reference_value = ""
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for meta_data in meta_data_copy:
      for key, value in meta_data.items():
         # If the key is in the list of possible reference names, then we found the reference
         if key in other_ref_names:
            reference_found = True
            reference_value = value
            # Remove the reference from the original meta data
            rule_meta_data.remove(meta_data)
         # If the value starts with http:// or https://, then we found the reference
         elif isinstance(value, str) and value.startswith(tuple(other_indicators)):
            reference_found = True
            reference_value = value
            # Remove the reference from the original meta data
            rule_meta_data.remove(meta_data)
   # If the reference is found, modify it
   if reference_found:
      rule_meta_data.append({'reference': reference_value})
   # If the reference is not found, add it
   if not reference_found:
      rule_meta_data.append({'reference': rule_set_url})
   return rule_meta_data


# Modify the YARA rule date
def align_yara_rule_date(rule_meta_data, repo_path, file_path):
   # List of possible date names
   date_names = ['date', 'created', 'created_at', 'creation_date', 'creation_time', 'creation', 'timestamp', 'time', 'datetime']
   modified_names = ['modified', 'last_modified', 'last_modified_at', 'last_modified_date', 'last_change', 'last_change_date', 'last_update', 'last_update_date', 'updated', 'updated_at', 'updated_date', 'updated_timestamp']
   # Look for the date in the rule meta data
   date_found = False
   date_value = "N/A" # Default date

   # GIT HISTORY -----------------------------------------------------------
   # We retrieve values from the git history that we can use in case we don't find these values in the meta data
   # Check if the date is in the cache
   if file_path in date_lookup_cache:
      # Debug info
      logging.log(logging.DEBUG, f"Retrieved date info for file {file_path} from cache.")
      (git_creation_date, git_modification_date) = date_lookup_cache[file_path]
   else:
      # Getting the last modification date of the rule file from the git log 
      # (this is not completely reliable, but better than nothing)
      (git_creation_date, git_modification_date) = get_rule_age_git(repo_path, file_path)
      if git_creation_date:
         # Add the date to the cache
         date_lookup_cache[file_path] = (git_creation_date, git_modification_date)

   # CREATION DATE -----------------------------------------------------------
   # We create a copy so that we can delete elements from the original
   meta_data_copy = rule_meta_data.copy()
   # Now we loop over the copy
   for meta_data in meta_data_copy:
      for key, value in meta_data.items():
         # If the key is in the list of possible date names, then we found the date
         if key in date_names:
            date_found = True
            date_created = dateparser.parse(value)
            # Remove the date from the original meta data
            rule_meta_data.remove(meta_data)
            rule_meta_data.append({'date': date_created.strftime("%Y-%m-%d")}) 

   # If the date is not found, try to get it from any of the meta data fields
   if not date_found:
      # Check if we find the date in a different value by looking for fields that contain a date
      for meta_data in meta_data_copy:
         for key, value in meta_data.items():
            # If the value contains a date, then we found the date
            if isinstance(value, str) and dateparser.parse(value):
               date_found = True
               date_created = dateparser.parse(value)
               # Remove the date from the original meta data
               rule_meta_data.remove(meta_data)
               rule_meta_data.append({'date': date_created.strftime("%Y-%m-%d")}) 

   # If the date was still not found, we try to get the date from the git log
   if not date_found:
      # Add the date to the rule meta data
      rule_meta_data.append({'date': git_creation_date.strftime("%Y-%m-%d")})

   # MODIFICATION DATE -----------------------------------------------------------
   # Now we check for a modification date
   modified_found = False
   for meta_data in meta_data_copy:
      for key, value in meta_data.items():
         # If the key is in the list of possible date names, then we found the date
         if key in modified_names:
            modified_found = True
            modified_value = dateparser.parse(value)
            # Remove the date from the original meta data
            rule_meta_data.remove(meta_data)
   # If the modified date was found and removed, add the new streamlined date value
   if modified_found:
      rule_meta_data.append({'modified': modified_value.strftime("%Y-%m-%d")})

   # If the modified date was still not found, we try to get the date from the git log
   if not modified_found:
      # Add the modified ate to the rule meta data
      rule_meta_data.append({'modified': git_modification_date.strftime("%Y-%m-%d")})

   return rule_meta_data


# Get the last modification date of the rule file from the git log
def get_rule_age_git(repo_path, file_path):

   # Initialize the repository object
   repo = Repo(repo_path)

   logging.log(logging.DEBUG, f"Repo path '{repo_path}'")
   logging.log(logging.DEBUG, f"Retrieving date info for file '{file_path}' from git log.")

   # Iterate over the commits that modified the file, and take the first one
   commits = list(repo.iter_commits(paths=file_path, max_count=1))
   if commits:
      first_commit = commits[-1]
      last_commit = commits[0]
      # Extract the datetime of the first commit that added the file
      creation_date = first_commit.committed_datetime
      # Extract the datetime of the last commit that modified the file
      modification_date = last_commit.committed_datetime
      logging.log(logging.DEBUG, f"Retrieved date info for file {file_path} from git log. Creation date: {creation_date}, Last modification: {modification_date}")
      # Return the date in the format YYYY-MM-DD
      return (creation_date, modification_date)
   else:
      print(f"No commits found for the file {file_path}.")
   return None