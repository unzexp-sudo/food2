#!/bin/sh
set -e
: "${PORT:=80}"
: "${API_PROXY_URL:=http://backend:8000}"
envsubst '$PORT $API_PROXY_URL' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
