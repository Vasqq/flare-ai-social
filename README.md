# ScribeChain

ScribeChain is your personal AI assistant for X Spaces built on Flare and Google Cloud, designed to keep you in the loop without needing to listen to hours of audio. Imagine catching the highlights of a live discussion with just a quick tweet!

## What ScribeChain Does

ScribeChain listens to live X Spaces and distills them into concise, informative summaries. Here's how it works:

1.  **Automatic Audio Download:** When you mention ScribeChain under a tweet containing a X Space link, it begins its work, downloading the audio.
2.  **Key Information Extraction:** Using the power of Gemini 2.0's multimodal capabilities, ScribeChain identifies the core takeaways from the audio input file, including announcements, insights, and calls to action.
3.  **Tweet Summary:** It then posts a well-structured summary as a reply, giving you the essential information in just a few seconds.
4.  **Follow-Up Q&A:** Have more questions? Simply reply to the summary tweet, and ScribeChain will dive back into the audio to provide detailed answers.

**Why Gemini 2.0?**

Gemini 2.0's ability to process both audio and text made it the perfect fit for ScribeChain. Gemini can reference the audio input directly without needing a text-to-speech API, saving time and aditional processing. More importantly, This multimodal input allows ScribeChain to truly understand the context of the conversation, ensuring you get the most relevant information.

## How to Use ScribeChain

Using ScribeChain is as simple as mentioning it on X:

1.  **Find a X Space:** Look for a tweet that contains a link to a completed X Space.
2.  **Mention ScribeChain:** Reply to that tweet with "@ScribeChain summarize".
3.  **Get Your Summary:** ScribeChain will process the audio and reply with a summary tweet.
4. **Ask follow up questions:** Reply to the summary tweet with your question.

## The Tech Behind ScribeChain

ScribeChain isn't just a clever idea; it's built on cutting-edge technology:

* **Gemini 2.0 Multimodal Input:** Powers the intelligent audio processing and summarization.
* **Trusted Execution Environments:** ScribeChain runs within a secure TEE on Google Cloud's Confidential Space. This ensures that your data and the AI's processing remain private and protected. TEEs create an isolated environment where code and data are shielded from unauthorized access, making them ideal for sensitive applications like AI agents.
* **Flare Integration (to be integrated):** ScribeChain is connected to Flare's TEEverifier contract, adding an extra layer of trust and transparency. This integration allows for on-chain verification of the AI's operations, making it a truly trustworthy social AI agent.

## The Main Loop Explained

Here's a simplified breakdown of how ScribeChain works behind the scenes:

```python
# ScribeChain's main loop
while True:
    # 1. Search for mentions of @ScribeChain
    tweets = search_twitter("@ScribeChain")

    # 2. Process new mentions
    for tweet in tweets:
        if "summarize" in tweet.text.lower():
            # 3. Download the Space audio
            audio_file = download_space_audio(tweet.parent_tweet.space_url)

            # 4. Generate a summary using Gemini 2.0
            summary = generate_summary(audio_file)

            # 5. Post the summary as a reply
            post_reply(summary, tweet.id)
        elif tweet.is_reply_to_summary:
          # 6. Generate a reply to the question using Gemini 2.0
          reply = generate_reply(tweet.question, tweet.summary_context)
          # 7. Post the reply as a reply.
          post_reply(reply, tweet.id)

    # 8. Wait for a while before checking again
    sleep(polling_interval)
```

## Known Quirks

* **Twitter API Rate Limits:** Due to the free tier of the Twitter API v2, ScribeChain's posting frequency is currently limited to approximately once every 15 minutes. 
* **Alpha Stage:** ScribeChain is currently in its alpha phase. This means it's still under active development, and you might encounter some rough edges. 
* **Audio Quality Dependence:** The accuracy of ScribeChain's summaries can be affected by the audio quality of the Twitter Space. Noisy or unclear audio may result in less accurate summaries.
* **Language Limitations:** Currently, ScribeChain is optimized for English-language Twitter Spaces. While it may provide summaries for other languages, the accuracy may vary.
* **Real-time Summaries:** ScribeChain currently processes completed Twitter Spaces. Real-time summarization is not yet supported.