import mxnet as mx
import caffe
from caffe import layers as CL
import json
import logging
logging.basicConfig(level=logging.DEBUG)

def convert_symbol2proto(symbol):
    def looks_like_weight(name):
        """Internal helper to figure out if node should be hidden with `hide_weights`.
        """
        if name.endswith("_weight"):
            return True
        if name.endswith("_bias"):
            return True
        if name.endswith("_beta") or name.endswith("_gamma") or name.endswith("_moving_var") or name.endswith(
                "_moving_mean"):
            return True
        return False

    json_symbol = json.loads(symbol.tojson())
    all_nodes = json_symbol['nodes']
    no_weight_nodes = []
    for node in all_nodes:
        op = node['op']
        name = node['name']
        if op == 'null':
            if looks_like_weight(name):
                continue
        no_weight_nodes.append(node)

    # build next node dict
    next_node = dict()
    for node in no_weight_nodes:
        node_name = node['name']
        for input in node['inputs']:
            last_node_name = all_nodes[input[0]]['name']
            if last_node_name in next_node:
                next_node[last_node_name].append(node_name)
            else:
                next_node[last_node_name] = [node_name]

    supported_op_type = ['null', 'BatchNorm', 'Convolution', 'Activation', 'Pooling', 'elemwise_add', 'SliceChannel',
                         'FullyConnected', 'SoftmaxOutput']
    top_dict = dict()
    caffe_net = caffe.NetSpec()
    for node in no_weight_nodes:
        if node['op'] == 'null':
            input_param = dict()
            if node['name'] == 'data':
                input_param['shape'] = dict(dim=[1, 3, 128, 128])
            else:
                input_param['shape'] = dict(dim=[1])
            top_data = CL.Input(ntop=1, input_param=input_param)
            top_dict[node['name']] = [top_data]
            setattr(caffe_net, node['name'], top_data)
        elif node['op'].endswith('_copy'):
            pass
        elif node['op'] == 'BatchNorm':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            attr = node['attr']
            in_place = False
            if len(next_node[bottom_node_name]) == 1:
                in_place = True
            bn_top = CL.BatchNorm(top_dict[bottom_node_name][input[1]], ntop=1,
                                  batch_norm_param=dict(use_global_stats=True,
                                                        moving_average_fraction=float(attr['momentum']),
                                                        eps=float(attr['eps'])), in_place=in_place)
            setattr(caffe_net, node['name'], bn_top)
            scale_top = CL.Scale(bn_top, ntop=1, scale_param=dict(bias_term=True), in_place=True)
            top_dict[node['name']] = [scale_top]
            setattr(caffe_net, node['name'] + '_scale', scale_top)
        elif node['op'] == 'Convolution':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            attr = node['attr']
            convolution_param = dict()
            if 'kernel' in attr:
                kernel_size = eval(attr['kernel'])
                assert kernel_size[0] == kernel_size[1]
                convolution_param['kernel_size'] = kernel_size[0]
            else:
                convolution_param['kernel_size'] = 1
            if 'no_bias' in attr:
                convolution_param['bias_term'] = False
            convolution_param['num_output'] = int(attr['num_filter'])
            if 'pad' in attr:
                pad_size = eval(attr['pad'])
                assert pad_size[0] == pad_size[1]
                convolution_param['pad'] = pad_size[0]
            if 'stride' in attr:
                stride_size = eval(attr['stride'])
                assert stride_size[0] == stride_size[1]
                convolution_param['stride'] = stride_size[0]
            conv_top = CL.Convolution(top_dict[bottom_node_name][input[1]], ntop=1, convolution_param=convolution_param)
            top_dict[node['name']] = [conv_top]
            setattr(caffe_net, node['name'], conv_top)
        elif node['op'] == 'Activation':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            attr = node['attr']
            in_place = False
            if len(next_node[bottom_node_name]) == 1:
                in_place = True
            ac_top = CL.ReLU(top_dict[bottom_node_name][input[1]], ntop=1, in_place=in_place)
            top_dict[node['name']] = [ac_top]
            setattr(caffe_net, node['name'], ac_top)
        elif node['op'] == 'Pooling':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            attr = node['attr']
            pooling_param = dict()
            if attr['pool_type'] == 'avg':
                pooling_param['pool'] = 1
            elif attr['pool_type'] == 'max':
                pooling_param['pool'] = 0
            else:
                assert False, attr['pool_type']
            if 'global_pool' in attr and eval(attr['global_pool']) is True:
                pooling_param['global_pooling'] = True
            else:
                if 'kernel' in attr:
                    kernel_size = eval(attr['kernel'])
                    assert kernel_size[0] == kernel_size[1]
                    pooling_param['kernel_size'] = kernel_size[0]
                if 'pad' in attr:
                    pad_size = eval(attr['pad'])
                    assert pad_size[0] == pad_size[1]
                    pooling_param['pad'] = pad_size[0]
                if 'stride' in attr:
                    stride_size = eval(attr['stride'])
                    assert stride_size[0] == stride_size[1]
                    pooling_param['stride'] = stride_size[0]
            pool_top = CL.Pooling(top_dict[bottom_node_name][input[1]], ntop=1, pooling_param=pooling_param)
            top_dict[node['name']] = [pool_top]
            setattr(caffe_net, node['name'], pool_top)
        elif node['op'] == 'elemwise_add':
            input_a = node['inputs'][0]
            while True:
                if all_nodes[input_a[0]]['op'] not in supported_op_type:
                    input_a = all_nodes[input_a[0]]['inputs'][0]
                else:
                    break
            input_b = node['inputs'][1]
            while True:
                if all_nodes[input_b[0]]['op'] not in supported_op_type:
                    input_b = all_nodes[input_b[0]]['inputs'][0]
                else:
                    break
            bottom_node_name_a = all_nodes[input_a[0]]['name']
            bottom_node_name_b = all_nodes[input_b[0]]['name']
            eltwise_param = dict()
            eltwise_param['operation'] = 1
            ele_add_top = CL.Eltwise(top_dict[bottom_node_name_a][input_a[1]], top_dict[bottom_node_name_b][input_b[1]],
                                     ntop=1, eltwise_param=eltwise_param)
            top_dict[node['name']] = [ele_add_top]
            setattr(caffe_net, node['name'], ele_add_top)
        elif node['op'] == 'SliceChannel':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            slice_param = dict()
            slice_param['slice_dim'] = 1
            slice_num = 2
            slice_outputs = CL.Slice(top_dict[bottom_node_name][input[1]], ntop=slice_num, slice_param=slice_param)
            top_dict[node['name']] = slice_outputs
            for idx, output in enumerate(slice_outputs):
                setattr(caffe_net, node['name'] + '_' + str(idx), output)
        elif node['op'] == 'FullyConnected':
            input = node['inputs'][0]
            while True:
                if all_nodes[input[0]]['op'] not in supported_op_type:
                    input = all_nodes[input[0]]['inputs'][0]
                else:
                    break
            bottom_node_name = all_nodes[input[0]]['name']
            attr = node['attr']
            inner_product_param = dict()
            inner_product_param['num_output'] = int(attr['num_hidden'])
            fc_top = CL.InnerProduct(top_dict[bottom_node_name][input[1]], ntop=1,
                                     inner_product_param=inner_product_param)
            top_dict[node['name']] = [fc_top]
            setattr(caffe_net, node['name'], fc_top)
        elif node['op'] == 'SoftmaxOutput':
            input_a = node['inputs'][0]
            while True:
                if all_nodes[input_a[0]]['op'] not in supported_op_type:
                    input_a = all_nodes[input_a[0]]['inputs'][0]
                else:
                    break
            input_b = node['inputs'][1]
            while True:
                if all_nodes[input_b[0]]['op'] not in supported_op_type:
                    input_b = all_nodes[input_b[0]]['inputs'][0]
                else:
                    break
            bottom_node_name_a = all_nodes[input_a[0]]['name']
            bottom_node_name_b = all_nodes[input_b[0]]['name']
            softmax_loss = CL.SoftmaxWithLoss(top_dict[bottom_node_name_a][input_a[1]],
                                              top_dict[bottom_node_name_b][input_b[1]], ntop=1)
            top_dict[node['name']] = [softmax_loss]
            setattr(caffe_net, node['name'], softmax_loss)
        else:
            logging.warn('unknown op type = %s' % node['op'])

    return caffe_net.to_proto()
