/*
 * VCP_DMA.h
 *
 *  Created on: 2021年1月19日
 *      Author: Joaquin
 */

#ifndef INC_VCP_DMA_H_
#define INC_VCP_DMA_H_

#include "dma.h"
#include "usbd_cdc_if.h"
#include  "stm32l4xx_hal_dma.h"


static int Bufferid_USB=0,Bufferid_DMA=1;


void CDC_DMA_Callbakset();
void CDC_DMA();
static void TransferComplete(DMA_HandleTypeDef *hdma_memtomem_dma2_stream0);



#endif /* INC_VCP_DMA_H_ */
