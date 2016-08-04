#!/usr/bin/env python3
import logging
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
import shutil
import asyncio
import os.path
import requests
from PIL import Image
from aiohttp import web
from gcloud import storage as gcs
from oauth2client.service_account import ServiceAccountCredentials
from pprint import pformat
import subprocess
from base64 import b64encode
import time
import platform


_is_linux = platform.system().lower() == 'linux'


_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(_root_dir)

logging.basicConfig(
    format='%(name)s - %(levelname)s - %(asctime)s %(message)s',
    level=logging.WARNING)
_logger = logging.getLogger('project-processor')
_logger.setLevel(logging.DEBUG)


def _process_job(data, jobs):
    temp_dir = '/tmp/muzhack/projects'
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    for entry in os.listdir(temp_dir):
        entry = os.path.join(temp_dir, entry)
        if entry not in [j['id'] for j in jobs]:
            _logger.debug(
                'Removing stale temporary directory \'{}\''.format(entry))
            shutil.rmtree(entry)
        else:
            _logger.debug(
                'Not removing temporary directory \'{}\' as it is in use'
                .format(entry))

    jobs.append(data)
    try:
        return _real_process_job(data, jobs, temp_dir)
    finally:
        del jobs[jobs.index(data)]


def _resize_image(original_image, width, height, original_fpath, suffix):
    _logger.debug('Resizing image to {}, {}'.format(width, height))
    target_image = Image.new('RGBA', (width, height), 'white')
    (original_width, original_height,) = original_image.size
    original_aspect_ratio = original_width / original_height

    target_aspect_ratio = width / height
    if original_aspect_ratio > target_aspect_ratio:
        _logger.debug('Padding target image by height')
        target_width = width
        target_height = width / original_aspect_ratio
        offset_x = 0
        offset_y = int((height - target_height) / 2)
    else:
        _logger.debug('Padding target image by width')
        target_height = height
        target_width = height * original_aspect_ratio
        offset_x = int((width - target_width) / 2)
        offset_y = 0

    target_width = int(target_width)
    target_height = int(target_height)
    resized_image = original_image.resize(
        (target_width, target_height,), resample=Image.LANCZOS)
    _logger.debug(
        'Resized original image to {}, {} before pasting into new picture'
        .format(resized_image.width, resized_image.height)
    )
    target_image.paste(
        resized_image,
        (offset_x, offset_y, offset_x + target_width,
            offset_y + target_height))
    extless_fpath, ext = os.path.splitext(original_fpath)
    fpath = '{}-{}{}'.format(extless_fpath, suffix, ext)
    _logger.debug('Saving resized image to \'{}\''.format(fpath))
    target_image.save(fpath)
    return fpath


def _process_picture(data):
    response = requests.get(data['url'], stream=True)
    if response.status_code == 200:
        fpath = data['name']
        with open(fpath, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)

        _logger.debug('Picture {} downloaded to \'{}\''.format(
            data['url'], fpath
        ))
        original_image = Image.open(fpath)
        explore_view_image_fpath = _resize_image(
            original_image, 218, 172, fpath, 'explore')
        thumbnail_image_fpath = _resize_image(
            original_image, 100, 82, fpath, 'thumb')
        main_image_fpath = _resize_image(
            original_image, 500, 409, fpath, 'main')
        _logger.debug('Finished processing picture {}, uploading...'.format(
            data['url']
        ))

        bucket = _gcs_client.bucket(_settings['GCLOUD_BUCKET'])
        for image_fpath in [
            explore_view_image_fpath, thumbnail_image_fpath,
            main_image_fpath,
        ]:
            directory = data['cloudPath'].rsplit('/', 1)[0]
            blob_path = '{}/{}'.format(
                directory, os.path.basename(image_fpath))
            _logger.debug('Uploading to {}'.format(blob_path))
            blob = bucket.blob(blob_path)
            blob.upload_from_filename(image_fpath)
            blob.make_public()

        _logger.debug('Success!')
        extless_url, url_ext = os.path.splitext(data['url'])
        return {**data, **{
            'thumbNailUrl': '{}-thumb{}'.format(extless_url, url_ext),
            'mainUrl': '{}-main{}'.format(extless_url, url_ext),
            'exploreUrl': '{}-explore{}'.format(extless_url, url_ext),
        }}
    else:
        if response.status_code == 404:
            _logger.warn('Couldn\'t find {}'.format(data['url']))
        else:
            _logger.warn('Failed to download {}: {}'.format(
                data['url'], error))
        response.raise_for_status()


def _process_instructions(data):
    _logger.debug('Processing build instructions...')

    instructions = data['instructions']
    bom = data.get('bom')
    if bom:
        bom_section = r"""<h1>Bill of Materials</h1>
{}
<hr>
""".format(bom)
    else:
        bom_section = ''
    instructions_pdf_source = r"""\
<html lang="en">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet"
    href="http://yui.yahooapis.com/pure/0.6.0/pure-min.css">
  <link rel="stylesheet" href="pdf.css">
</head>
<body>
<div class="container">
{}
<div id="instructions">
{}
</div>
</div>
</body>
</html>
""".format(bom_section, instructions)
    with open('instructions.html', 'wt') as f:
        f.write(instructions_pdf_source)

    with open('pdf.css', 'wt') as f:
        f.write("""\
.container {
  width: 100%;
  padding: 60px;
  box-sizing: border-box;
}

.container > :first-child {
  margin-top: 0;
}

hr {
  margin-top: 20px;
  margin-bottom: 20px;
}

#instructions > :first-child {
  margin-top: 0;
}

img {
  max-width: 100%;
  display: block;
  margin-bottom: 10px;
  margin-top: 10px;
  border-radius: 4px;
}

table {
  border-collapse: collapse;
  border-spacing: 0;
  empty-cells: show;
  border: 1px solid #cbcbcb;
}

thead {
  background-color: #e0e0e0;
  color: #000;
  text-align: left;
  vertical-align: bottom;
}

td:first-child, th:first-child {
  border-left-width: 0;
}

td, th {
  padding: 0.5em 1em;
  border-left: 1px solid #cbcbcb;
  border-width: 0 0 0 1px;
  font-size: inherit;
  margin: 0;
  overflow: visible;
  padding: .5em 1em;
}

td {
  background-color: transparent;
}

tr:nth-child(2n-1) td {
  background-color: #f2f2f2;
}
""")

    start = time.time()

    document_title = '{} Build Instructions'.format(data['title'])
    command = [
        'wkhtmltopdf', '-q', '--title', document_title,
        'instructions.html', 'instructions.pdf',
    ]
    if _is_linux:
        # Needed to run wkhtmltopdf headless
        command.insert(0, 'xvfb-run')
    subprocess.check_call(command)
    time_taken = time.time() - start
    _logger.debug(
        'Successfully processed build instructions in {} second(s)'
        .format(time_taken))

    bucket = _gcs_client.bucket(_settings['GCLOUD_BUCKET'])
    directory = data['cloudDirectory']
    blob_path = '{}/{}'.format(directory, '{}-instructions.pdf'.format(
        data['id'].replace('/', '-')))
    _logger.debug('Uploading to {}'.format(blob_path))
    blob = bucket.blob(blob_path)
    blob.upload_from_filename('instructions.pdf')
    blob.make_public()

    return blob_path


def _real_process_job(data, jobs, temp_dir):
    _logger.debug('Processing job {}'.format(pformat(data)))

    orig_dir = os.getcwd()
    new_dir = os.path.join(temp_dir, data['id'])
    os.makedirs(new_dir)
    os.chdir(new_dir)
    process_results = {}
    picture_results = []
    try:
        for picture in data['pictures']:
            picture_results.append(_process_picture(picture))
        process_results['pictures'] = picture_results

        process_results['instructionsPdfPath'] = _process_instructions(data)
    finally:
        os.chdir(orig_dir)

    _logger.debug('Finished processing')
    return process_results


async def _add_job(request):
    """Add job to queue."""
    _logger.debug('Received request to add job')
    data = await request.json()
    _logger.debug('Received json: {}'.format(data))

    result = await _loop.run_in_executor(None, _process_job, data, _jobs_list)
    return web.Response(
        text=json.dumps(result), content_type='application/json')


def _load_settings():
    key_filter = [
        'GCLOUD_PROJECT_ID',
        'GCLOUD_PRIVATE_KEY_ID',
        'GCLOUD_PRIVATE_KEY',
        'GCLOUD_CLIENT_EMAIL',
        'GCLOUD_CLIENT_ID',
        'GCLOUD_BUCKET',
    ]
    if os.path.exists('settings.json'):
        with open('settings.json') as f:
            json_dict = json.load(f)
            settings = {k: v for k, v in json_dict.items() if k in key_filter}
    else:
        settings = {k: os.environ[k] for k in key_filter}
    return settings


_settings = _load_settings()

_credentials = ServiceAccountCredentials.from_json_keyfile_dict({
    'type': 'service_account',
    'client_email': _settings['GCLOUD_CLIENT_EMAIL'],
    'private_key': _settings['GCLOUD_PRIVATE_KEY'],
    'private_key_id': _settings['GCLOUD_PRIVATE_KEY_ID'],
    'client_id': _settings['GCLOUD_CLIENT_ID'],
})
_gcs_client = gcs.Client(
    project=_settings['GCLOUD_PROJECT_ID'], credentials=_credentials)

_loop = asyncio.get_event_loop()
_max_workers = 1
_loop.set_default_executor(ProcessPoolExecutor(max_workers=_max_workers))
_manager = multiprocessing.Manager()
_jobs_list = _manager.list()

_app = web.Application(logger=_logger)
_app.router.add_route('POST', '/jobs', _add_job)

web.run_app(_app, port=10000)
