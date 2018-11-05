# ansible-plugin-callback-elasticsearch

## Synopsis

* Generate JSON objects and send them to Elasticsearch via CMRESHandler plugin
* Uses a YAML configuration file that ends with elastic.(yml|yaml)

### Requirements

This below requirements are needed on the local node that executes this plugin.

* Ansible > 2.7.0
* sudo pip3 install [python-dateutil](https://pypi.org/project/python-dateutil/)
* sudo pip3 install [CMRESHandler](https://github.com/cmanaha/python-elasticsearch-logger)

## Parameters

    plugin: 'elastic'
    host: '172.17.0.2'
    port: 9200
    username: 'elastic'
    password: 'changeme'
    auth_type: 1
    use_ssl: False
    verify_ssl: False
    es_index_name: 'ansible-test'
    app: 'ansible'
    env: 'dev'
    raise_on_indexing_exceptions: True
    es_logger_name: 'ansible'

# Tests
    docker run -d -p 9200:9200 -p 9300:9300 --name elasticsearch docker.elastic.co/elasticsearch/elasticsearch:5.6.12
    docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' elasticsearch
    docker run -d -p 5601:5601 --name kibana --link elasticsearch docker.elastic.co/kibana/kibana:5.6.12

## Status
### Author
    Michael Hatoum <michael@adaltas.com>
