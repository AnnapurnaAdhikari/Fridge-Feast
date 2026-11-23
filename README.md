# Fridge Feast

A Streamlit cooking assistant that turns your available ingredients into dinner
ideas, with recipe images and YouTube links.

## Run locally

1. Install Python 3.12 or later.
2. Install dependencies:

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

3. Create `.streamlit/secrets.toml` with your Gemini API key:

   ```toml
   API_KEY = "your-gemini-api-key"
   ```

4. Start the app:

   ```powershell
   py -3 -m streamlit run app.py
   ```

Keep `.streamlit/secrets.toml` private. It is excluded from Git.

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository for this project and upload the app files. Do not
   upload `.streamlit/secrets.toml`.
2. Confirm `.gitignore` is included so local secrets and Python cache files stay
   out of the repository.
3. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app
   from that repository and select `app.py` as the entry point. Choose Python
   3.12 in the app's advanced settings.
4. Open the app's **Settings → Secrets** and add:

   ```toml
   API_KEY = "your-new-gemini-api-key"
   ```

5. Save and deploy. Community Cloud installs the packages listed in
   `requirements.txt`.

If the key has ever been exposed, revoke it and create a replacement before
deploying. Never put API keys in the repository or app code.
