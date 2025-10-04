#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from app import create_app

logging.basicConfig(level=getattr(logging, 'INFO', logging.INFO))

app = create_app()
app.logger.setLevel(logging.INFO)

if __name__ == "__main__":
    app.run(host = app.config['SERVER_HOST'], port = app.config['SERVER_PORT'])
