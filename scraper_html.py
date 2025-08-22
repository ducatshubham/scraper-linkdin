# from bs4 import BeautifulSoup
# import pandas as pd

# # ---------- Input ----------
# html_file_path = input("Enter path of saved LinkedIn HTML file: ").strip()
# company_name = input("Enter Company Name (e.g., PromptHire.ai): ").strip()
# role_name = input("Enter Role/Designation (e.g., Full Stack Developer): ").strip()

# # ---------- Read HTML ----------
# with open(html_file_path, 'r', encoding='utf-8') as f:
#     soup = BeautifulSoup(f, 'html.parser')

# # ---------- Extract profiles ----------
# data = []
# # LinkedIn search result profiles usually have <a> with href containing '/in/'
# for a_tag in soup.find_all('a', href=True):
#     href = a_tag['href']
#     name = a_tag.get_text().strip()
#     if '/in/' in href and name:
#         # avoid duplicates
#         if not any(d['Profile URL'] == href for d in data):
#             data.append({
#                 "Name": name,
#                 "Profile URL": href
#             })

# # ---------- Save CSV ----------
# filename = f"{company_name}_{role_name}_profiles.csv".replace(" ", "_")
# df = pd.DataFrame(data)
# df.to_csv(filename, index=False)
# print(f"\nTotal {len(data)} profiles saved to {filename}")
