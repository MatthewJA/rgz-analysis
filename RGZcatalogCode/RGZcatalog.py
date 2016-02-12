'''
RGZcatalog is a pipeline that takes all of the completed RGZ subjects and creates a Mongo database containing
consensus matching information, radio morphology, IR counterpart location, and data from corresponding
AllWISE and SDSS catalogs.
'''

import logging, urllib2, time, argparse, json, os
from pymongo import MongoClient
import numpy as np
from StringIO import StringIO
from gzip import GzipFile
from ast import literal_eval
from astropy.io import fits
from astropy import wcs, coordinates as coord, units as u
import astroquery
from astroquery.irsa import Irsa

#custom modules for the RGZ catalog
import catalogFunctions as fn #contains custom functions
import contourNode as c #contains Node class
from updateConsensus import updateConsensus #replaces the current consensus collection with a specified csv

# Set up the local data paths. Currently works from UMN servers on tabernacle, plus
# Kyle Willett's laptop.

def determine_paths(paths):

    found_path = False
    for path in paths:
        if os.path.exists(path):
            found_path = True
            return path

    if found_path == False:
        print "Unable to find the hardcoded local path:"
        print paths
        return None

rgz_path = determine_paths(('/Users/willettk/Astronomy/Research/GalaxyZoo/rgz-analysis','/data/tabernacle/larry/RGZdata/rgz-analysis'))
data_path = determine_paths(('/Volumes/REISEPASS/','/data/extragal/willett'))

@profile
def RGZcatalog():

    #begin logging even if not run from command line
    logging.basicConfig(filename='RGZcatalog.log', level=logging.DEBUG, format='%(asctime)s %(message)s')
    logging.captureWarnings(True)

    #check if consensus collection needs to be updated; if so, drop entire consensus collection and replace it with entries from the designated CSV file
    parser = argparse.ArgumentParser()
    parser.add_argument('--consensus', help='replace the current consensus collection with a specified csv')
    args = parser.parse_args()
    if args.consensus:
        updateConsensus(args.consensus)

    #connect to database of subjects
    logging.info('Connecting to MongoDB')
    db = MongoClient()['radio']
    subjects = db['radio_subjects']
    consensus = db['consensus']
    catalog = db['catalog'] #this is being populated by this program

    if catalog.count():
        logging.info('Catalog contains entries; appending')

    #get dictionary for finding the path to FITS files and WCS headers
    with open('%s/first_fits.txt' % rgz_path) as f:
        lines = f.readlines()
    pathdict = {}
    for l in lines:
        spl = l.split(' ')
        pathdict[spl[1].strip()] = '%s/rgz/raw_images/RGZ-full.%i/FIRST-IMGS/%s.fits' % (data_path, int(spl[0]), spl[1].strip())

    #count the number of entries from this run and how many entries are in the catalog total
    count = 0
    if catalog.count() != 0:
        for entry in catalog.find().sort('catalog_id', -1).limit(1):
            IDnumber = entry['catalog_id']
    else:
        IDnumber = 0

    #start timer
    starttime = time.time()

    #iterate through all subjects
    for subject in subjects.find().batch_size(10):
    #for subject in subjects.find({'zooniverse_id': {'$in': ['ARG00000sl', 'ARG0003f9l']} }):
    #for subject in subjects.find({'zooniverse_id':'ARG00000sl'}): #sample subject with distinct galaxies
    #for subject in subjects.find({'zooniverse_id':'ARG0003f9l'}): #sample subject with multiple components

        logging.info('Processing subject field %s', subject['zooniverse_id'])

        for consensusObject in consensus.find({'zooniverse_id':subject['zooniverse_id']}):
            
            #do not process if this object in this field is already in the catalog
            process = True
            for i in catalog.find({'Zooniverse_id':subject['zooniverse_id']}):
                if i['consensus']['label'] == consensusObject['label']:
                    process = False

            logging.info('Processing consensus object %s within subject field %s', consensusObject['label'].upper(), subject['zooniverse_id'])

            if process:

                count += 1
                IDnumber += 1

                #display which entry is being processed to see how far the program is
                print IDnumber
                entry = {'catalog_id':IDnumber, 'Zooniverse_id':str(subject['zooniverse_id'])}
                
                #find location of FITS file
                fid = consensusObject['FIRST_id']
                if fid[0] == 'F':
                    fits_loc = pathdict[fid]
                    entry.update({'FIRST_id':str(fid)})
                else:
                    fits_loc = '%s/rgz/raw_images/ATLAS/2x2/%s_radio.fits' % (data_path,fid)
                    entry.update({'ATLAS_id':str(fid)})
                
                #find IR counterpart from consensus data, if present
                w = wcs.WCS(fits.open(fits_loc)[0].header) #gets pixel-to-WCS conversion from header
                ir_coords = literal_eval(consensusObject['ir_peak'])
                if ir_coords == (-99, -99):
                    ir_pos = None
                    wise_match = None
                    sdss_match = None
                else:
                    p2w = w.wcs_pix2world
                    ir_ra_pixels = ir_coords[0] * 132./500.
                    ir_dec_pixels = 133 - ir_coords[1] * 132./500.
                    ir_peak = p2w( np.array([[ir_ra_pixels, ir_dec_pixels]]), 1)
                    ir_pos = coord.SkyCoord(ir_peak[0][0], ir_peak[0][1], unit=(u.deg,u.deg), frame='icrs')

                entry.update({'consensus':{'n_users':consensusObject['n_users'], 'n_total':consensusObject['n_total'], \
                                           'level':consensusObject['consensus_level'], 'label':consensusObject['label']}})
                if ir_pos:
                    logging.info('IR counterpart found')
                    entry['consensus'].update({'IR_ra':ir_pos.ra.deg, 'IR_dec':ir_pos.dec.deg})
                else:
                    logging.info('No IR conterpart found')

                #if an IR peak exists, search AllWISE and SDSS for counterparts
                if ir_pos:
                    
                    #get IR data from AllWISE Source Catalog
                    tryCount = 0
                    while(True): #in case of error, wait 10 sec and try again; give up after 5 tries
                        tryCount += 1
                        try:
                            table = Irsa.query_region(ir_pos, catalog='wise_allwise_p3as_psd', radius=3*u.arcsec)
                            break
                        except astroquery.exceptions.TimeoutError as e:
                            if tryCount>5:
                                logging.exception('Too many AllWISE query errors')
                                raise
                            logging.exception(e)
                            time.sleep(10)
                    if len(table):
                        numberMatches = 0
                        if table[0]['w1snr']>5:
                            match = table[0]
                            dist = match['dist']
                            numberMatches += 1
                        else:
                            match = None
                            dist = np.inf
                        if len(table)>1:
                            for row in table:
                                if row['dist']<dist and row['w1snr']>5:
                                    match = row
                                    dist = match['dist']
                                    numberMatches += 1
                        if match:
                            wise_match = {'designation':'WISEA'+match['designation'], 'ra':match['ra'], 'dec':match['dec'], 'numberMatches':np.int16(numberMatches), \
                                          'w1mpro':match['w1mpro'], 'w1sigmpro':match['w1sigmpro'], 'w1snr':match['w1snr'], \
                                          'w2mpro':match['w2mpro'], 'w2sigmpro':match['w2sigmpro'], 'w2snr':match['w2snr'], \
                                          'w3mpro':match['w3mpro'], 'w3sigmpro':match['w3sigmpro'], 'w3snr':match['w3snr'], \
                                          'w4mpro':match['w4mpro'], 'w4sigmpro':match['w4sigmpro'], 'w4snr':match['w4snr']}     
                        else:
                            wise_match = None
                    else:
                        wise_match = None
                        
                    if wise_match:
                        logging.info('AllWISE match found')
                        for key in wise_match.keys():
                            if wise_match[key] is np.ma.masked:
                                    wise_match.pop(key)
                            elif wise_match[key] and type(wise_match[key]) is not str:
                                wise_match[key] = wise_match[key].item()
                            elif wise_match[key] == 0:
                                wise_match[key] = 0
                        entry.update({'AllWISE':wise_match})
                    else:
                        logging.info('No AllWISE match found')

                    #get optical magnitude data from Galaxy table in SDSS
                    query = '''select objID, ra, dec, u, g, r, i, z, err_u, err_g, err_r, err_i, err_z from Galaxy
                               where (ra between ''' + str(ir_pos.ra.deg) + '-1.5/3600 and ' + str(ir_pos.ra.deg) + '''+1.5/3600) and
                                     (dec between ''' + str(ir_pos.dec.deg) + '-1.5/3600 and ' + str(ir_pos.dec.deg) + '+1.5/3600)'
                    df = fn.SDSS_select(query)
                    if len(df):
                        numberMatches = 0
                        matchPos = coord.SkyCoord(df.iloc[0]['ra'], df.iloc[0]['dec'], unit=(u.deg, u.deg))
                        tempDist = ir_pos.separation(matchPos).arcsecond
                        if tempDist<3:
                            match = df.iloc[0]
                            dist = tempDist
                            numberMatches += 1
                        else:
                            match = None
                            dist = np.inf
                        if len(df)>1:
                            for i in range(len(df)):
                                matchPos = coord.SkyCoord(df.iloc[i]['ra'], df.iloc[i]['dec'], unit=(u.deg, u.deg))
                                tempDist = ir_pos.separation(matchPos).arcsecond
                                if tempDist<3 and tempDist<dist:
                                    match = df.iloc[i]
                                    dist = tempDist
                                    numberMatches += 1
                        if match is not None:
                            sdss_match = {'objID':df['objID'][match.name], 'ra':match['ra'], 'dec':match['dec'], 'numberMatches':np.int16(numberMatches), \
                                          'u':match['u'], 'g':match['g'], 'r':match['r'], 'i':match['i'], 'z':match['z'], \
                                          'u_err':match['err_u'], 'g_err':match['err_g'], 'r_err':match['err_r'], 'i_err':match['err_i'], 'z_err':match['err_z']}
                        else:
                            sdss_match = None
                    else:
                        sdss_match = None

                    #only get more data from SDSS if a postional match exists
                    #get photo redshift and uncertainty from Photoz table
                    #get spectral lines from GalSpecLine table
                    #get spectral class and spec redshift and uncertainty from SpecPhoto table
                    if sdss_match:

                        query = '''select p.z as photoZ, p.zErr as photoZErr, s.z as specZ, s.zErr as specZErr,
                                     oiii_5007_flux, oiii_5007_flux_err, h_beta_flux, h_beta_flux_err,
                                     nii_6584_flux, nii_6584_flux_err, h_alpha_flux, h_alpha_flux_err,
                                     case when class like 'GALAXY' then 0
                                          when class like 'QSO' then 1
                                          when class like 'STAR' then 2 end as spectralClass
                                   from Photoz as p
                                     full outer join SpecObj as s on p.objID = s.bestObjID
                                     full outer join GalSpecLine as g on s.specobjid = g.specobjid
                                   where p.objID = ''' + str(sdss_match['objID'])
                        df = fn.SDSS_select(query)
                        if len(df):
                            if not np.isnan(df['specZ'][0]):
                                redshift = df['specZ'][0]
                                redshift_err = df['specZErr'][0]
                                redshift_type = np.int16(1)
                            else:
                                redshift = df['photoZ'][0]
                                redshift_err = df['photoZErr'][0]
                                redshift_type = np.int16(0)
                            if redshift != -9999:
                                moreData = {'redshift':redshift, 'redshift_err':redshift_err, 'redshift_type':redshift_type}
                            else:
                                moreData = {}
                            for key in ['oiii_5007_flux', 'oiii_5007_flux_err', 'h_beta_flux', 'h_beta_flux_err', \
                                        'nii_6584_flux', 'nii_6584_flux_err', 'h_alpha_flux', 'h_alpha_flux_err', 'spectralClass']:
                                if not np.isnan(df[key][0]):
                                    moreData[key] = df[key][0]
                            sdss_match.update(moreData)

                    if sdss_match:
                        logging.info('SDSS match found')
                        for key in sdss_match.keys():
                            if sdss_match[key] is None:
                                sdss_match.pop(key)
                            elif sdss_match[key] and type(sdss_match[key]) is not str:
                                sdss_match[key] = sdss_match[key].item()
                            elif sdss_match[key] == 0:
                                sdss_match[key] = 0
                        entry.update({'SDSS':sdss_match})
                    else:
                        logging.info('No SDSS match found')

                #end of 'if IR peak'

                #try block attempts to read JSON from web; if it exists, calculate data
                try:
                    link = subject['location']['contours'] #gets url as Unicode string

                    tryCount = 0
                    while(True): #in case of error, wait 10 sec and try again; give up after 5 tries
                        tryCount += 1
                        try:
                            compressed = urllib2.urlopen(str(link)).read() #reads contents of url to str
                            break
                        except (urllib2.URLError, urllib2.HTTPError) as e:
                            if tryCount>5:
                                logging.exception('Too many radio query errors')
                                raise
                            logging.exception(e)
                            time.sleep(10)
                        
                    tempfile = StringIO(compressed) #temporarily stores contents as file (emptied after unzipping)
                    uncompressed = GzipFile(fileobj=tempfile, mode='r').read() #unzips contents to str
                    data = json.loads(uncompressed) #loads JSON object

                    #create list of trees, each containing a contour and its contents
                    contourTrees = []
                    consensusBboxes = literal_eval(consensusObject['bbox'])
                    for contour in data['contours']:
                        for bbox in consensusBboxes:
                            if fn.approx(contour[0]['bbox'][0], bbox[0]) and fn.approx(contour[0]['bbox'][1], bbox[1]) and \
                               fn.approx(contour[0]['bbox'][2], bbox[2]) and fn.approx(contour[0]['bbox'][3], bbox[3]):
                                tree = c.Node(contour=contour, fits_loc=fits_loc)
                                contourTrees.append(tree)

                    #get component fluxes and sizes
                    components = []
                    for tree in contourTrees:
                        bboxP = fn.bboxToDS9(fn.findBox(tree.value['arr']), tree.imgSize)[0] #bbox in DS9 coordinate pixels
                        bboxCornersRD = tree.w.wcs_pix2world( np.array( [[bboxP[0],bboxP[1]], [bboxP[2],bboxP[3]] ]), 1) #two opposite corners of bbox in ra and dec
                        raRange = [ min(bboxCornersRD[0][0], bboxCornersRD[1][0]), max(bboxCornersRD[0][0], bboxCornersRD[1][0]) ]
                        decRange = [ min(bboxCornersRD[0][1], bboxCornersRD[1][1]), max(bboxCornersRD[0][1], bboxCornersRD[1][1]) ]
                        pos1 = coord.SkyCoord(raRange[0], decRange[0], unit=(u.deg, u.deg))
                        pos2 = coord.SkyCoord(raRange[1], decRange[1], unit=(u.deg, u.deg))
                        extentArcmin = pos1.separation(pos2).arcminute
                        solidAngleArcsec2 = tree.areaArcsec2
                        components.append({'flux':tree.fluxmJy, 'fluxErr':tree.fluxErrmJy, 'angularExtent':extentArcmin, 'solidAngle':solidAngleArcsec2, \
                                           'raRange':raRange, 'decRange':decRange})

                    #adds up total flux of all components
                    totalFluxmJy = 0
                    totalFluxErrmJy2 = 0
                    for component in components:
                        totalFluxmJy += component['flux']
                        totalFluxErrmJy2 += pow(component['fluxErr'], 2)
                    totalFluxErrmJy = np.sqrt(totalFluxErrmJy2)

                    #finds total area enclosed by contours in arcminutes
                    totalSolidAngleArcsec2 = 0
                    for component in components:
                        totalSolidAngleArcsec2 += component['solidAngle']

                    #find maximum extent of component bboxes in arcseconds
                    maxAngularExtentArcmin = 0
                    if len(components)==1:
                        maxAngularExtentArcmin = components[0]['angularExtent']
                    else:
                        for i in range(len(components)-1):
                            for j in range(1,len(components)-i):
                                corners1 = [ [components[i]['raRange'][0],components[i]['decRange'][0]], \
                                             [components[i]['raRange'][0],components[i]['decRange'][1]], \
                                             [components[i]['raRange'][1],components[i]['decRange'][0]], \
                                             [components[i]['raRange'][1],components[i]['decRange'][1]] ]
                                corners2 = [ [components[i+j]['raRange'][0],components[i+j]['decRange'][0]], \
                                             [components[i+j]['raRange'][0],components[i+j]['decRange'][1]], \
                                             [components[i+j]['raRange'][1],components[i+j]['decRange'][0]], \
                                             [components[i+j]['raRange'][1],components[i+j]['decRange'][1]] ]
                                for corner1 in corners1:
                                    for corner2 in corners2:
                                        pos1 = coord.SkyCoord(corner1[0], corner1[1], unit=(u.deg, u.deg))
                                        pos2 = coord.SkyCoord(corner2[0], corner2[1], unit=(u.deg, u.deg))
                                        angularExtentArcmin = pos1.separation(pos2).arcminute
                                        if angularExtentArcmin>maxAngularExtentArcmin:
                                            maxAngularExtentArcmin = angularExtentArcmin

                    #add all peaks up into single list
                    peakList = []
                    for tree in contourTrees:
                        for peak in tree.peaks:
                            peakList.append(peak)
                    peakFluxErrmJy = contourTrees[0].sigmamJy

                    entry.update({'radio':{'totalFlux':totalFluxmJy, 'totalFluxErr':totalFluxErrmJy, 'outermostLevel':data['contours'][0][0]['level']*1000, \
                            'numberComponents':len(contourTrees), 'numberPeaks':len(peakList), 'maxAngularExtent':maxAngularExtentArcmin, \
                            'totalSolidAngle':totalSolidAngleArcsec2, 'peakFluxErr':peakFluxErrmJy, 'peaks':peakList, 'components':components}})

                    #calculate physical data using redshift
                    if sdss_match:
                        if 'redshift' in sdss_match:
                            z = sdss_match['redshift']
                            lz = np.log10(z)
                            DAkpc = pow(10, -0.0799*pow(lz,3)-0.406*pow(lz,2)+0.3101*lz+3.2239)*1000 #angular size distance approximation in kpc
                            DLkpc = DAkpc*pow(1+z, 2) #luminosity distance approximation in kpc
                            maxPhysicalExtentKpc = DAkpc*maxAngularExtentArcmin*np.pi/180/60 #arcminutes to radians
                            totalCrossSectionKpc2 = pow(DAkpc,2)*totalSolidAngleArcsec2*pow(np.pi/180/3600,2) #arcseconds^2 to radians^2
                            totalLuminosityWHz = totalFluxmJy*1e-29*4*np.pi*pow(DLkpc*3.09e19,2) #mJy to W/(m^2 Hz), kpc to m
                            totalLuminosityErrWHz = totalFluxErrmJy*1e-29*4*np.pi*pow(DLkpc*3.09e19,2)
                            peakLuminosityErrWHz = peakFluxErrmJy*1e-29*4*np.pi*pow(DLkpc*3.09e19,2)
                            for component in components:
                                component['physicalExtent'] = DAkpc*component['angularExtent']*np.pi/180/3600
                                component['crossSection'] = pow(DAkpc,2)*component['solidAngle']*pow(np.pi/180/3600,2)
                                component['luminosity'] = component['flux']*1e-29*4*np.pi*pow(DLkpc*3.09e19,2)
                                component['luminosityErr'] = component['fluxErr']*1e-29*4*np.pi*pow(DLkpc*3.09e19,2)
                            for peak in peakList:
                                peak['luminosity'] = peak['flux']*1e-29*4*np.pi*pow(DLkpc*3.09e19,2)
                            entry['radio'].update({'maxPhysicalExtent':maxPhysicalExtentKpc, 'totalCrossSection':totalCrossSectionKpc2, \
                                                   'totalLuminosity':totalLuminosityWHz, 'totalLuminosityErr':totalLuminosityErrWHz, \
                                                   'peakLuminosityErr':peakLuminosityErrWHz})

                    logging.info('Radio data added')
                                       
                #if the link doesn't have a JSON, no data can be determined
                except urllib2.HTTPError as e:
                    if e.code == 404:
                        logging.info('No radio JSON detected')
                    else:
                        logging.exception(e)
                        raise

                catalog.insert(entry)
                logging.info('Entry %i added to catalog', IDnumber)

    #end of subject search

    #end timer
    endtime = time.time()
    output = 'Time taken: ' + str(endtime-starttime)
    logging.info(output)
    print output

    return count

if __name__ == '__main__':
    logging.basicConfig(filename='RGZcatalog.log', level=logging.DEBUG, format='%(asctime)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.captureWarnings(True)
    logging.info('Catalog run from command line')
    try:
        output = str(RGZcatalog()) + ' entries added.'
        logging.info(output)
        print output
    except BaseException as e:
        logging.exception(e)
        raise
