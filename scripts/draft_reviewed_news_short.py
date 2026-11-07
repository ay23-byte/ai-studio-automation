import argparse
from core.news_shorts_writer import generate_reviewed_shorts
def main():
 p=argparse.ArgumentParser(description='Draft a Short from a human-approved source packet.')
 p.add_argument('reviewed_packet'); p.add_argument('--output',default='output/daily_news/shorts_script_draft.json')
 a=p.parse_args(); result=generate_reviewed_shorts(a.reviewed_packet,a.output)
 print('Draft saved:',a.output,'| scenes:',len(result['scenes']),'| words:',result['word_count'])
 print('Human script, factual and visual review remains required; this command does not render or upload.')
if __name__=='__main__': main()