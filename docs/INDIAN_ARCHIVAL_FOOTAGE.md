# Indian archival footage sources

This project supports Indian archival material through a **manual, permission-first import** workflow. It does not scrape archive websites or assume public access means reuse permission.

## Recommended repositories

### National Cultural Audiovisual Archives (NCAA / IGNCA)
- Search: https://ncaa.gov.in/repository/search/searchHome
- Advanced search: https://ncaa.gov.in/repository/search/advancedSearchHome
- Copyright policy: https://ncaa.gov.in/repository/common/copyright
- Collection focus: oral traditions, performing arts, rituals, crafts, music, dance and cultural practices.
- Rights: the portal's copyright policy says content may not be reproduced without due permission from IGNCA and the concerned partnering institution. Obtain written permission for the exact item and intended YouTube use before importing.

### Prasar Bharati Archives / Doordarshan Archives
- Main archive: https://prasarbharati.gov.in/en/prasar-bharati-archives/
- DD video archive: https://prasarbharati.gov.in/en/prasar-bharati-archives/dd-archives/
- Footage licensing instructions: https://prasarbharati.gov.in/wp-content/uploads/2024/04/new-doc.pdf
- Collection focus: Independence Day, Republic Day, speeches, national leaders, interviews, documentaries, cultural programmes and historic broadcasts.
- Rights: footage is supplied under item-specific commercial or non-commercial licences. The published instructions state that supplied footage is restricted to the film/programme named in the application and may not be resold, transferred or reused for another programme. Obtain a licence that explicitly covers the intended YouTube programme and monetization.

### National Archives of India (Abhilekh Patal)
- Search: https://www.abhilekh-patal.in/
- Best for documents, maps, photographs and historical research rather than video footage. The National Archives FAQ says it does not maintain a separate audiovisual archive. Check item-specific reproduction conditions before using digitized images in a video.

## Importing a licensed clip

First obtain the original file from the archive through its authorized process and retain the written permission/licence. Then run:

    python -m scripts.register_indian_archive_asset "path/to/clip.mp4" --provider "NCAA" --title "Exact archive item title" --source-url "https://ncaa.gov.in/repository/..." --licence-reference "Permission email or licence reference" --usage-scope "Commercial YouTube use including monetization" --creator "Creator or partner institution" --attribution "Exact required credit"

For Prasar Bharati, change provider to "Prasar Bharati Archives" and use the exact official item URL and licence details.

The importer rejects non-official item URLs, missing permission references, and scopes that do not explicitly cover commercial/monetized YouTube use. It records the licence reference, usage scope, expiry and attribution in a .source.json sidecar.

**Important:** Keep the actual licence/permission document with the project records. A text reference in the sidecar is an audit pointer, not a substitute for the signed licence. Recheck whether a Prasar Bharati licence permits the specific video/programme and whether it permits repeated use.
